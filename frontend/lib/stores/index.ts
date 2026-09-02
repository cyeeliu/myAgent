// Barrel for the new zustand stores (method-routed /ws architecture).
// Reach explicitly as `../stores/index` — there is no legacy `lib/stores.ts`.
export { useChatStore } from './chatStore';
export type { PendingQuestion } from './chatStore';
export { useSessionStore } from './sessionStore';
export { useTodoStore } from './todoStore';
