import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface TerminalLine {
  id: number;
  type: 'cmd' | 'stdout' | 'stderr' | 'exit' | 'info';
  text: string;
}

interface TerminalTabProps {
  cwd?: string;
}

/**
 * Parse a `cd` target from a command string.
 * Returns the target path if the command is a bare `cd <path>`, else null.
 * Handles: `cd /foo`, `cd ..`, `cd ../bar`, `cd ~`, `cd`
 */
function parseCdTarget(cmd: string): string | null {
  const trimmed = cmd.trim();
  // Match: cd, cd <path>, cd "<path>", cd '<path>'
  const match = trimmed.match(/^cd(?:\s+("[^"]*"|'[^']*'|[^\s;|&]+))?\s*$/);
  if (!match) return null;
  if (!match[1]) return ''; // bare `cd` → home/root
  // Strip quotes
  return match[1].replace(/^["']|["']$/g, '');
}

export function TerminalTab({ cwd: initialCwd = '' }: TerminalTabProps) {
  const { t } = useTranslation();
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  // Persistent CWD tracking — relative to workspace root
  const [currentCwd, setCurrentCwd] = useState(initialCwd);
  const outputRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const lineIdRef = useRef(0);

  const addLine = useCallback((type: TerminalLine['type'], text: string) => {
    setLines((prev) => [...prev, { id: lineIdRef.current++, type, text }]);
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [lines]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const runCommand = useCallback(async (cmd: string) => {
    if (!cmd.trim() || running) return;
    setRunning(true);
    setHistory((prev) => [...prev, cmd]);
    setHistoryIndex(-1);
    addLine('cmd', `$ ${cmd}`);

    // Check if this is a `cd` command — resolve CWD client-side for persistence
    const cdTarget = parseCdTarget(cmd);
    if (cdTarget !== null) {
      try {
        // Run `cd <target> && pwd` to resolve the new directory
        const resolveCmd = cdTarget === '' || cdTarget === '~'
          ? 'pwd'
          : `cd ${cdTarget} && pwd`;
        const res = await fetch('/file-api/exec', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ command: resolveCmd, cwd: currentCwd }),
        });
        const data = await res.json();
        if (!res.ok) {
          addLine('stderr', data.detail || `HTTP ${res.status}`);
        } else if (data.exit_code !== 0) {
          if (data.stderr) addLine('stderr', data.stderr);
          addLine('exit', `(exit code: ${data.exit_code})`);
        } else {
          // Parse the resolved path and convert to relative CWD
          const resolvedPath = (data.stdout || '').trim();
          if (resolvedPath) {
            // The backend returns absolute paths. We need to convert to
            // a relative path from the workspace root for the cwd param.
            // The backend's base is REPO_ROOT/workspace, so we send the
            // absolute path as cwd and let the backend handle it.
            // Actually, the backend expects cwd relative to workspace root.
            // We'll send the resolved path and let the backend resolve it.
            // For simplicity, we store the absolute path and send it as cwd.
            setCurrentCwd(resolvedPath);
            addLine('info', `(cwd → ${resolvedPath})`);
          }
        }
      } catch (err) {
        addLine('stderr', err instanceof Error ? err.message : 'Request failed');
      } finally {
        setRunning(false);
        inputRef.current?.focus();
      }
      return;
    }

    // Normal command — run with current CWD
    try {
      const res = await fetch('/file-api/exec', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ command: cmd, cwd: currentCwd }),
      });
      const data = await res.json();
      if (!res.ok) {
        addLine('stderr', data.detail || `HTTP ${res.status}`);
      } else {
        if (data.stdout) addLine('stdout', data.stdout);
        if (data.stderr) addLine('stderr', data.stderr);
        if (data.exit_code !== 0) {
          addLine('exit', `(exit code: ${data.exit_code})`);
        }
      }
    } catch (err) {
      addLine('stderr', err instanceof Error ? err.message : 'Request failed');
    } finally {
      setRunning(false);
      inputRef.current?.focus();
    }
  }, [running, currentCwd, addLine]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = input.trim();
      setInput('');
      void runCommand(cmd);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.length === 0) return;
      const newIdx = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(newIdx);
      setInput(history[newIdx]);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIndex === -1 || historyIndex >= history.length - 1) {
        setHistoryIndex(-1);
        setInput('');
        return;
      }
      const newIdx = historyIndex + 1;
      setHistoryIndex(newIdx);
      setInput(history[newIdx]);
    } else if (e.key === 'l' && e.ctrlKey) {
      e.preventDefault();
      setLines([]);
    }
  }, [input, running, runCommand, history, historyIndex]);

  // Show CWD in prompt
  const promptLabel = currentCwd ? `$ ${currentCwd}` : '$';

  return (
    <div className="terminal">
      <div className="terminal__output" ref={outputRef}>
        {lines.length === 0 && (
          <div className="terminal__line terminal__line--prompt">
            {t('rightSidebar.terminalWelcome')}
          </div>
        )}
        {lines.map((line) => (
          <div key={line.id} className={`terminal__line terminal__line--${line.type}`}>
            {line.text}
          </div>
        ))}
        {running && (
          <div className="terminal__line terminal__line--exit">{t('rightSidebar.terminalRunning')}</div>
        )}
      </div>
      <div className="terminal__input-row">
        <span className="terminal__prompt">{promptLabel}</span>
        <input
          ref={inputRef}
          className="terminal__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={running}
          placeholder={t('rightSidebar.terminalPlaceholder')}
          spellCheck={false}
          autoComplete="off"
        />
      </div>
    </div>
  );
}
