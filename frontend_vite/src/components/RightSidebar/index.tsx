import { useCallback, useRef, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { FileViewer } from '../AgentPanel/FileViewer';
import { FileTreeTab } from './FileTreeTab';
import { TerminalTab } from './TerminalTab';
import './RightSidebar.css';

type SidebarTab = 'files' | 'editor' | 'terminal';

interface RightSidebarProps {
  width: number;
  onResize: (width: number) => void;
}

export function RightSidebar({ width, onResize }: RightSidebarProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SidebarTab>('files');
  const [selectedFile, setSelectedFile] = useState<{ path: string; name: string } | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  // Resize handling
  const resizingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    resizingRef.current = true;
    startXRef.current = e.clientX;
    startWidthRef.current = width;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [width]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!resizingRef.current) return;
      // Dragging left increases width (mouse moves left = sidebar grows)
      const delta = startXRef.current - e.clientX;
      const newWidth = Math.max(280, Math.min(600, startWidthRef.current + delta));
      onResize(newWidth);
    };
    const handleMouseUp = () => {
      if (resizingRef.current) {
        resizingRef.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [onResize]);

  const handleSelectFile = useCallback((path: string, name: string) => {
    setSelectedFile({ path, name });
    setActiveTab('editor');
  }, []);

  const handleRefresh = useCallback(() => {
    setReloadNonce((n) => n + 1);
  }, []);

  return (
    <>
      <div
        className={`right-sidebar-resizer ${resizingRef.current ? 'right-sidebar-resizer--dragging' : ''}`}
        onMouseDown={handleResizeStart}
      />
      <div className="right-sidebar" style={{ width }}>
        {/* Header with tabs */}
        <div className="right-sidebar__header">
          <div className="right-sidebar__tabs">
            <button
              className={`right-sidebar__tab ${activeTab === 'files' ? 'right-sidebar__tab--active' : ''}`}
              onClick={() => setActiveTab('files')}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 7.5c0-.414.336-.75.75-.75h4.5c.414 0 .75.336.75.75v9c0 .414-.336.75-.75.75H3a.75.75 0 01-.75-.75v-9zM9.75 7.5c0-.414.336-.75.75-.75h4.5c.414 0 .75.336.75.75v9c0 .414-.336.75-.75.75h-4.5a.75.75 0 01-.75-.75v-9zM16.5 7.5c0-.414.336-.75.75-.75H21a.75.75 0 01.75.75v9a.75.75 0 01-.75.75h-3.75a.75.75 0 01-.75-.75v-9z" />
              </svg>
              {t('rightSidebar.tabFiles')}
            </button>
            <button
              className={`right-sidebar__tab ${activeTab === 'editor' ? 'right-sidebar__tab--active' : ''}`}
              onClick={() => setActiveTab('editor')}
              disabled={!selectedFile}
              style={!selectedFile ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125" />
              </svg>
              {t('rightSidebar.tabEditor')}
            </button>
            <button
              className={`right-sidebar__tab ${activeTab === 'terminal' ? 'right-sidebar__tab--active' : ''}`}
              onClick={() => setActiveTab('terminal')}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 7.5l3 3-3 3m4.5 0h6M4.5 4.5h15a1.5 1.5 0 011.5 1.5v12a1.5 1.5 0 01-1.5 1.5h-15a1.5 1.5 0 01-1.5-1.5V6a1.5 1.5 0 011.5-1.5z" />
              </svg>
              {t('rightSidebar.tabTerminal')}
            </button>
          </div>
          <div className="right-sidebar__actions">
            {activeTab === 'editor' && (
              <button className="right-sidebar__btn" onClick={handleRefresh} title={t('common.refresh')}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.5m0 0v-4.5m0 0l-4.5 4.5M3.75 12a8.25 8.25 0 1114.485 5.13M3 14.652l4.5-4.5m0 0H3m0 0v4.5" />
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* Body */}
        <div className="right-sidebar__body">
          {activeTab === 'files' && (
            <FileTreeTab
              onSelectFile={handleSelectFile}
              selectedFilePath={selectedFile?.path ?? null}
            />
          )}
          {activeTab === 'editor' && (
            selectedFile ? (
              <FileViewer
                key={selectedFile.path}
                filePath={selectedFile.path}
                fileName={selectedFile.name}
                reloadNonce={reloadNonce}
              />
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--muted)', fontSize: '13px' }}>
                {t('rightSidebar.noFileSelected')}
              </div>
            )
          )}
          {activeTab === 'terminal' && (
            <TerminalTab />
          )}
        </div>
      </div>
    </>
  );
}
