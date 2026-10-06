import "@testing-library/jest-dom/vitest";

// jsdom has no layout, so it has no ResizeObserver. The map only uses it to follow its container.
class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= FakeResizeObserver;

// The mode a device chose is kept in localStorage, so one test must not leak it into the next.
afterEach(() => window.localStorage.clear());
