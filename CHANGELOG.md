# Changelog

## 1.2.0 (2026-06-30)

- Full Ollama library browser (235+ models with descriptions, capabilities, pull counts)
- Search, filter by capability (vision, tools, thinking, embedding), and sort models
- Paginated model grid with one-click install
- Model tag explorer — click "Tags" on any model to see all available variants/quantizations
- Manual install input on Browse and Downloads pages (type any model:tag)
- Running models panel on Installed page with auto-polling
- New sidebar tab layout: Dashboard → Browse Models → Scan & Recommend → Installed → Downloads → Chat
- Backend: cached Ollama library scraper with 1-hour TTL, `/api/library/browse`, `/api/library/tags`, `/api/ollama/ps` endpoints

## 1.1.0 (2026-06-30)

- Markdown rendering in chat (code blocks, bold, italic, lists)
- Model detail modal instead of inline append
- Toast notification system for installs, errors, and export
- Sort recommendations by score, VRAM, context, or name
- Export recommendations to CSV
- Keyboard shortcuts (Ctrl+Enter to send chat, Esc to close modals)
- Auto-load chat models when navigating to chat page
- Live Ollama status polling (every 10s)
- Search/filter installed models
- Mobile-responsive sidebar with toggle button
- Loading spinners for async operations
- Clear chat button with confirmation
- Security: XSS hardening with escHtml() throughout
- Security: Removed all inline onclick handlers
- Security: Fixed ollama:// protocol (now copies run command)
- Bugfix: Download progress now updates the correct item's title
- Bugfix: Fixed GitHub URLs from anomalyco → Dkrynen

## 1.0.0 (2026-06-30)

- Initial public release
- Hardware scanning (GPU, VRAM, RAM, CPU) for Windows/Linux/macOS
- 65 curated models across all major families
- Smart recommendation engine with quality/speed/fit/context scoring
- Ollama integration for model pull, list, delete, and chat
- Built-in streaming chat interface
- Dashboard with system overview and quick picks
