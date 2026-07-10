// todoStore — the agent's task list (TaskCreate/TaskUpdate items). The gateway
// surfaces todo updates as `todo.updated` events in jiuwenswarm; myAgent's
// agent_core doesn't emit those yet, so this store is wired but mostly idle —
// it exists so the multi-panel shell can render a todos panel and the chatStore
// can forward subtask updates without a separate dependency.
import { create } from 'zustand';
import type { TodoItem } from '../types/message';

interface TodoState {
  todos: TodoItem[];
  setTodos: (todos: TodoItem[]) => void;
  upsert: (todo: TodoItem) => void;
  updateStatus: (id: string, status: TodoItem['status'], activeForm?: string) => void;
  clear: () => void;
}

export const useTodoStore = create<TodoState>((set) => ({
  todos: [],
  setTodos: (todos) => set({ todos }),
  upsert: (todo) => set((s) => {
    const exists = s.todos.some((t) => t.id === todo.id);
    return {
      todos: exists
        ? s.todos.map((t) => (t.id === todo.id ? todo : t))
        : [...s.todos, todo],
    };
  }),
  updateStatus: (id, status, activeForm) => set((s) => ({
    todos: s.todos.map((t) =>
      t.id === id ? { ...t, status, activeForm } : t),
  })),
  clear: () => set({ todos: [] }),
}));
