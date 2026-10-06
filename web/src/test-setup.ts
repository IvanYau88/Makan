import "@testing-library/jest-dom/vitest";

// jsdom has no layout, so it has no ResizeObserver. The map only uses it to follow its container.
class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= FakeResizeObserver;
