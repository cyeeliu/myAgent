"use client";
import { useEffect, useState, useRef, useCallback } from "react";

type Skill = { name: string; description: string };

const STATIC_COMMANDS = [
  { name: "/help", description: "Show help information" },
  { name: "/clear", description: "Clear conversation history" },
  { name: "/model", description: "Switch model" },
  { name: "/skills", description: "List available skills" },
  { name: "/agents", description: "Manage agents" },
  { name: "/memory", description: "View memory" },
  { name: "/tasks", description: "Show task list" },
  { name: "/compact", description: "Compact conversation context" },
];

interface SlashCommandMenuProps {
  onSelect: (command: string) => void;
  onClose: () => void;
}

export function SlashCommandMenu({ onSelect, onClose }: SlashCommandMenuProps) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [filter, setFilter] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const allCommands = [
    ...STATIC_COMMANDS,
    ...skills.map(s => ({ name: `/${s.name}`, description: s.description })),
  ];

  const filtered = allCommands.filter(cmd =>
    cmd.name.toLowerCase().includes(filter.toLowerCase()) ||
    cmd.description.toLowerCase().includes(filter.toLowerCase())
  );

  useEffect(() => {
    // Fetch skills from API
    const fetchSkills = async () => {
      try {
        const res = await fetch("/api/skills");
        const data = await res.json();
        if (Array.isArray(data)) {
          setSkills(data);
        }
      } catch (err) {
        console.warn("Failed to fetch skills", err);
      }
    };
    fetchSkills();
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex(prev => (prev + 1) % filtered.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex(prev => (prev - 1 + filtered.length) % filtered.length);
      } else if (e.key === "Enter" && filtered.length > 0) {
        e.preventDefault();
        onSelect(filtered[selectedIndex].name);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [filtered, selectedIndex, onSelect, onClose]);

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
    }
  }, []);

  useEffect(() => {
    setSelectedIndex(0);
  }, [filter]);

  const handleSelect = useCallback((cmd: string) => {
    onSelect(cmd);
  }, [onSelect]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16" onClick={onClose}>
      <div
        ref={menuRef}
        className="w-full max-w-md rounded-xl border border-paper-300 bg-paper-50 shadow-lg"
        onClick={e => e.stopPropagation()}
      >
        <div className="border-b border-paper-300/70 p-3">
          <div className="flex items-center gap-2">
            <span className="text-paper-500">/</span>
            <input
              ref={inputRef}
              type="text"
              value={filter}
              onChange={e => setFilter(e.target.value.slice(1))} // Remove leading slash
              placeholder="搜索命令…"
              className="flex-1 bg-transparent text-sm text-paper-900 outline-none placeholder:text-paper-500"
              autoComplete="off"
              spellCheck="false"
            />
          </div>
        </div>
        <div className="max-h-64 overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <div className="px-3 py-2 text-sm text-paper-500">无匹配命令</div>
          ) : (
            filtered.map((cmd, idx) => (
              <button
                key={cmd.name}
                className={`flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left transition ${
                  idx === selectedIndex ? "bg-clay-100 text-paper-900" : "hover:bg-paper-200"
                }`}
                onClick={() => handleSelect(cmd.name)}
                onMouseEnter={() => setSelectedIndex(idx)}
              >
                <div className="flex-1">
                  <div className="font-medium text-paper-900">{cmd.name}</div>
                  <div className="mt-0.5 text-xs text-paper-600">{cmd.description}</div>
                </div>
                {idx === selectedIndex && (
                  <div className="text-xs text-paper-500">↵</div>
                )}
              </button>
            ))
          )}
        </div>
        <div className="border-t border-paper-300/70 px-3 py-2 text-xs text-paper-500">
          <div className="flex items-center justify-between">
            <span>↑↓ 导航 · ↵ 选择 · Esc 关闭</span>
            <span>{filtered.length} 个命令</span>
          </div>
        </div>
      </div>
    </div>
  );
}