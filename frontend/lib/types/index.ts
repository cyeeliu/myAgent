// Barrel for the new wire + domain types. Reach explicitly as `../types/index`
// to avoid shadowing the existing `lib/types.ts` (legacy event-frame types still
// used by the current ChatPanel/Sidebar during the migration).
export * from './websocket';
export * from './message';
