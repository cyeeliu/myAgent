import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface FileEntry {
  name: string;
  path: string;
  isMarkdown: boolean;
  isDirectory: boolean;
}

interface FileTreeTabProps {
  onSelectFile: (path: string, name: string) => void;
  selectedFilePath: string | null;
}

interface TreeNode {
  entry: FileEntry;
  children: FileEntry[] | null; // null = not loaded, [] = loaded empty
  expanded: boolean;
}

export function FileTreeTab({ onSelectFile, selectedFilePath }: FileTreeTabProps) {
  const { t } = useTranslation();
  const [rootEntries, setRootEntries] = useState<FileEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Track expanded directories and their children
  const [expandedDirs, setExpandedDirs] = useState<Map<string, FileEntry[]>>(new Map());
  const [loadingDirs, setLoadingDirs] = useState<Set<string>>(new Set());

  // Fetch root directory listing
  const fetchDir = useCallback(async (dir: string): Promise<FileEntry[]> => {
    const res = await fetch(`/file-api/list-files?dir=${encodeURIComponent(dir)}`, {
      cache: 'no-store',
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    return (data.files ?? []) as FileEntry[];
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const entries = await fetchDir('agent/workspace');
        if (!cancelled) {
          setRootEntries(entries);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [fetchDir]);

  const toggleDir = useCallback(async (entry: FileEntry) => {
    const dirPath = entry.path;
    if (expandedDirs.has(dirPath)) {
      // Collapse
      setExpandedDirs((prev) => {
        const next = new Map(prev);
        next.delete(dirPath);
        return next;
      });
      return;
    }
    // Expand — fetch children if not already loaded
    if (!loadingDirs.has(dirPath)) {
      setLoadingDirs((prev) => new Set(prev).add(dirPath));
      try {
        const children = await fetchDir(dirPath);
        setExpandedDirs((prev) => new Map(prev).set(dirPath, children));
      } catch {
        // ignore — user can retry by collapsing and re-expanding
      } finally {
        setLoadingDirs((prev) => {
          const next = new Set(prev);
          next.delete(dirPath);
          return next;
        });
      }
    }
  }, [expandedDirs, loadingDirs, fetchDir]);

  const handleSelect = useCallback((entry: FileEntry) => {
    if (entry.isDirectory) {
      void toggleDir(entry);
    } else {
      onSelectFile(entry.path, entry.name);
    }
  }, [toggleDir, onSelectFile]);

  const renderNode = (entry: FileEntry, depth: number): React.ReactNode => {
    const indent = { paddingLeft: `${8 + depth * 14}px` };
    const isExpanded = expandedDirs.has(entry.path);
    const isLoading = loadingDirs.has(entry.path);
    const isActive = selectedFilePath === entry.path;

    return (
      <div key={entry.path}>
        <div
          className={`file-tree__node ${entry.isDirectory ? 'file-tree__node--dir' : ''} ${isActive ? 'file-tree__node--active' : ''}`}
          style={indent}
          onClick={() => handleSelect(entry)}
        >
          {entry.isDirectory ? (
            <span className={`file-tree__chevron ${isExpanded ? 'file-tree__chevron--open' : ''}`}>
              {isLoading ? '⋯' : '▶'}
            </span>
          ) : (
            <span className="file-tree__chevron" />
          )}
          <span className="file-tree__icon">
            {entry.isDirectory ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} style={{ width: 14, height: 14 }}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 7.5c0-.414.336-.75.75-.75h4.5c.414 0 .75.336.75.75v9c0 .414-.336.75-.75.75H3a.75.75 0 01-.75-.75v-9zM9.75 7.5c0-.414.336-.75.75-.75h4.5c.414 0 .75.336.75.75v9c0 .414-.336.75-.75.75h-4.5a.75.75 0 01-.75-.75v-9zM16.5 7.5c0-.414.336-.75.75-.75H21a.75.75 0 01.75.75v9a.75.75 0 01-.75.75h-3.75a.75.75 0 01-.75-.75v-9z" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} style={{ width: 14, height: 14 }}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
              </svg>
            )}
          </span>
          <span className="file-tree__name">{entry.name}</span>
        </div>
        {entry.isDirectory && isExpanded && expandedDirs.has(entry.path) && (
          expandedDirs.get(entry.path)!.map((child) => renderNode(child, depth + 1))
        )}
      </div>
    );
  };

  if (loading) {
    return <div className="file-tree__loading">{t('rightSidebar.loading')}</div>;
  }
  if (error) {
    return <div className="file-tree__empty">{t('rightSidebar.error')}: {error}</div>;
  }
  if (!rootEntries || rootEntries.length === 0) {
    return <div className="file-tree__empty">{t('rightSidebar.emptyWorkspace')}</div>;
  }

  return (
    <div className="file-tree">
      {rootEntries.map((entry) => renderNode(entry, 0))}
    </div>
  );
}
