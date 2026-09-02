import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface TerminalLine {
  id: number;
  type: 'cmd' | 'stdout' | 'stderr' | 'exit';
  text: string;
}

interface TerminalTabProps {
  cwd?: string;
}

export function TerminalTab({ cwd = '' }: TerminalTabProps) {
  const { t } = useTranslation();
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
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

    try {
      const res = await fetch('/file-api/exec', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ command: cmd, cwd }),
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
  }, [running, cwd, addLine]);

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
        <span className="terminal__prompt">$</span>
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
