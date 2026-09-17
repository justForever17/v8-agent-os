# Design references

Terminal interaction research consulted QwenLM/qwen-code at commit
`b8def02aadfc384ecb860909155d196267c4fa0c` (2026-09-16, package 0.24.0):
`KeypressContext.tsx`, `shared/text-buffer.ts`, `shared/VirtualizedList.tsx`,
and `layouts/ScreenReaderAppLayout.tsx`. These files are Apache-2.0 licensed.
No source code was copied. V8OS implements its own small input and viewport
model and continues to use V8OS Engine and session-realtime as authorities.

Ink, React, and string-width are used under their distributed licenses.
The bundled shared projection and local Engine client are V8OS MIT code.
