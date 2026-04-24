# 项目编程 / subagent SYSTEM_CONTENT 快照

## 场景配置摘要
```json
{
  "transport": "chat",
  "scope": "project_workspace",
  "engineeringMode": "force",
  "sessionId": "stress-project-2026-04-24_11-28-55",
  "runId": "run-project-2026-04-24_11-28-55",
  "currentScope": "project:test1",
  "scopeChain": [
    "global",
    "project:test1"
  ],
  "projectId": "test1",
  "workspaceId": "test1",
  "workspacePath": "E:\\Projects\\test1",
  "historyTurns": 13
}
```

## 路由与诊断摘要
```json
{
  "selectedAgent": {
    "id": "implementation-engineer",
    "name": "Implementation Engineer",
    "toolMode": "contextual_auto"
  },
  "selectionDiagnostics": {
    "selectionReason": "strong_capability_match",
    "selectionConfidence": 1.0,
    "matchSignals": [
      "domain:software_engineering",
      "operation:implementation",
      "behavior:runtime_governance",
      "writeSet:apps/v8-agent-os-engine/runtimes/extensions/runtime.py,apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py",
      "agentClass:executor",
      "plannerSuitability:medium",
      "engineeringRole:implementation",
      "lexical:12"
    ],
    "targetId": "implementation-engineer"
  },
  "route": {
    "mode": "stage1_only",
    "skillInventoryRevision": "6e8af47d17884aec448d30d126f4b61ed2c3602a",
    "visibleRootSignature": "ef142a909082bf0e6f437937d2653133db0c6c5a",
    "visibleRootRevisionKey": "7c2010083a0e0ef83dd9d80a35ddc47933695ddd",
    "visibleRegistryCacheHit": false,
    "inventoryReadyState": "ready",
    "snapshotFreshness": "live",
    "inventoryBarrierApplied": true,
    "inventoryBarrierWaitMs": 0.0,
    "inventoryBarrierTimedOut": false,
    "dirtyVisibleRoots": [],
    "changedRoots": [
      "E:\\Projects\\test1\\.agents\\skills"
    ],
    "scopedRefreshMode": "live_overlay",
    "mcpInventoryRevision": "cold",
    "skillRefreshMode": "delta",
    "mcpRefreshMode": "",
    "mcpChangedServers": {},
    "recentSkillDiscoveryCount": 36,
    "recentSkillKeepaliveCount": 2,
    "inventoryRefreshDurationMs": {
      "skills": 33.56,
      "mcp": null
    },
    "skillsRoutingMode": "stage1_only",
    "mcpRoutingMode": "stage1_only",
    "pluginHostRoutingMode": "lexical_shortlist",
    "modelId": "deepseek-chat",
    "role": "extensions_prefilter",
    "reason": "Stage 2 已关闭，直接使用第 1 层 shortlist。",
    "prefilterTimedOut": false,
    "prefilterCacheHit": false,
    "queryAnalysisCacheHit": false,
    "lexiconSignature": "lexicon:54434ba2b829f9f3",
    "lexiconCoreSignature": "lexicon-core:bde85a6f34f2023f",
    "lexiconLocales": [
      "en",
      "zh-CN",
      "de",
      "es",
      "ja",
      "ko",
      "ru"
    ],
    "lexiconLoadErrors": [],
    "marketLexiconEnabled": true,
    "marketLexiconSignature": "lexicon-market:93a844a01f030718",
    "marketLexiconLocales": [
      "en",
      "zh-CN"
    ],
    "marketLexiconLoadErrors": [],
    "marketLexiconHitTerms": [],
    "marketLexiconContributionScore": 0,
    "stage1Enabled": {
      "skills": true,
      "mcp": true
    },
    "stage1TopK": {
      "skills": 10,
      "mcp": 10
    },
    "stage2Enabled": {
      "skills": false,
      "mcp": false
    },
    "stage2TopK": {
      "skills": 5,
      "mcp": 2
    },
    "llmTimeoutSeconds": {
      "skills": 10,
      "mcp": 5
    },
    "routingMode": "stage1_only",
    "skills": [
      "internal-comms",
      "find-skills",
      "darwin-skill",
      "munger-perspective",
      "elon-musk-perspective",
      "llm-video",
      "huashu-nuwa",
      "huashu-speech-coach",
      "doc-coauthoring",
      "vercel-react-best-practices"
    ],
    "selectedSkillIds": [
      "global:c1a63ed2ed99a675",
      "global:9bdbcd9561ed3ab7",
      "global:c0f140bfdcd7e5cb",
      "global:915a434eef063ad5",
      "global:beeb1ed76d4df463",
      "global:15f18c5fcf5d256c",
      "global:67cb9ebfa7543040",
      "global:ed7024257d0b0f51",
      "global:f45c1cf2ca76d568",
      "global:2acae9709d3c1c02"
    ],
    "artifactIntent": "code",
    "documentSubIntent": null,
    "operationIntent": "edit",
    "directCanonicalFamilies": [],
    "canonicalFamilies": [],
    "primaryCanonicalFamily": null,
    "shortCanonicalNarrowing": false,
    "shortCanonicalNarrowingApplied": false,
    "primaryThemeIntents": [
      "engineering_ai"
    ],
    "secondaryThemeHints": [],
    "rankingSignals": {
      "artifactAnchor": true,
      "documentSubIntent": null,
      "operationIntent": true,
      "topicTokenCount": 34
    },
    "themeRankingSignals": {
      "themeIntent": true,
      "secondaryThemeHints": 0,
      "artifactAnchorPresent": true,
      "fallbackInjectedCount": 0
    },
    "profileMatchedCount": 10,
    "profileBackfilledCount": 2,
    "themeMatchedCount": 9,
    "themeBackfilledCount": 2,
    "mcpProfileMatchedCount": 0,
    "pluginHostProfileMatchedCount": 0,
    "mcpThemeMatchedCount": 0,
    "pluginHostThemeMatchedCount": 0,
    "mcpDocumentSubIntentMatched": 0,
    "pluginHostDocumentSubIntentMatched": 0,
    "mcpThemeFallbackInjectedCount": 0,
    "pluginHostThemeFallbackInjectedCount": 0,
    "skillStage1Entries": [
      {
        "skillId": "global:dae6cfe92904923f",
        "skillName": "webapp-testing",
        "description": "Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\webapp-testing",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\webapp-testing\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "C:\\Users\\sunny\\.agents\\skills\\webapp-testing\\scripts",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "C:\\Users\\sunny\\.agents\\skills\\webapp-testing\\examples",
        "availableFiles": [
          "scripts/",
          "scripts/with_server.py",
          "examples/",
          "examples/console_logging.py",
          "examples/element_discovery.py",
          "examples/static_html_automation.py"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "workflow_or_script",
          "primaryArtifactTypes": [
            "code"
          ],
          "primaryOperations": [],
          "interactionMode": "workflow",
          "capabilityConfidence": 0.68,
          "profileSource": "rules",
          "secondaryArtifactHints": [],
          "secondaryOperationHints": [
            "search",
            "automate",
            "create"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "code": [
                "script",
                "scripts"
              ]
            },
            "operationMatches": {},
            "classMatches": {
              "integration_or_tooling": [
                "server"
              ],
              "workflow_or_script": [
                "script",
                "scripts"
              ]
            },
            "secondaryArtifacts": {},
            "secondaryOperations": {
              "search": [
                "find"
              ],
              "automate": [
                "automation",
                "api"
              ],
              "create": [
                "create"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:3d121b50aee7b28d",
        "skillName": "algorithmic-art",
        "description": "Creating algorithmic art using p5.js with seeded randomness and interactive parameter exploration. Use this when users request creating art using code, generative art, algorithmic art, flow fields, or particle systems. Create original algorithmic art rather than copying existing artists' work to avoid copyright violations.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\algorithmic-art",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\algorithmic-art\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "C:\\Users\\sunny\\.agents\\skills\\algorithmic-art\\templates",
        "examplesDir": "",
        "availableFiles": [
          "templates/",
          "templates/generator_template.js",
          "templates/viewer.html"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "code"
          ],
          "primaryOperations": [
            "create"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.97,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "document",
            "image"
          ],
          "secondaryOperationHints": [
            "guide"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "code": [
                "code"
              ]
            },
            "operationMatches": {
              "create": [
                "create",
                "creating",
                "creation"
              ]
            },
            "classMatches": {},
            "secondaryArtifacts": {
              "document": [
                "md"
              ],
              "image": [
                "images"
              ]
            },
            "secondaryOperations": {
              "guide": [
                "guide",
                "how to"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:b2fdc7c6c24cc4b2",
        "skillName": "frontend-design",
        "description": "Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, artifacts, posters, or applications (examples include websites, landing pages, dashboards, React components, HTML/CSS layouts, or when styling/beautifying any web UI). Generates creative, polished code and UI design that avoids generic AI aesthetics.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\frontend-design",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\frontend-design\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "code"
          ],
          "primaryOperations": [
            "create"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.93,
          "profileSource": "rules",
          "secondaryArtifactHints": [],
          "secondaryOperationHints": [],
          "evidenceSignals": {
            "artifactMatches": {
              "code": [
                "code",
                "coding"
              ]
            },
            "operationMatches": {
              "create": [
                "create",
                "build",
                "creation"
              ]
            },
            "classMatches": {},
            "secondaryArtifacts": {},
            "secondaryOperations": {}
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:7094a33adcc16f13",
        "skillName": "ui-ux-pro-max",
        "description": "UI/UX design intelligence. 50 styles, 21 palettes, 50 font pairings, 20 charts, 9 stacks (React, Next.js, Vue, Svelte, SwiftUI, React Native, Flutter, Tailwind, shadcn/ui). Actions: plan, build, create, design, implement, review, fix, improve, optimize, enhance, refactor, check UI/UX code. Projects: website, landing page, dashboard, admin panel, e-commerce, SaaS, portfolio, blog, mobile app, .html, .tsx, .vue, .svelte. Elements: button, modal, navbar, sidebar, card, table, form, chart. Styles: glassmorphism, claymorphism, minimalism, brutalism, neumorphism, bento grid, dark mode, responsive, skeuomorphism, flat design. Topics: color palette, accessibility, animation, layout, typography, font pairing, spacing, hover, shadow, gradient. Integrations: shadcn/ui MCP for component search and examples.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\ui-ux-pro-max",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\ui-ux-pro-max\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "C:\\Users\\sunny\\.agents\\skills\\ui-ux-pro-max\\scripts",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "code"
          ],
          "primaryOperations": [
            "create"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.87,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "spreadsheet",
            "image"
          ],
          "secondaryOperationHints": [
            "guide",
            "search",
            "analyze",
            "automate"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "code": [
                "code"
              ]
            },
            "operationMatches": {
              "create": [
                "create",
                "build"
              ]
            },
            "classMatches": {
              "integration_or_tooling": [
                "mcp"
              ],
              "methodology_or_tutorial": [
                "guide"
              ]
            },
            "secondaryArtifacts": {
              "spreadsheet": [
                "table"
              ],
              "image": [
                "image",
                "images"
              ]
            },
            "secondaryOperations": {
              "guide": [
                "guide",
                "guidance",
                "how to"
              ],
              "search": [
                "search"
              ],
              "analyze": [
                "review"
              ],
              "automate": [
                "cli"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:2acae9709d3c1c02",
        "skillName": "vercel-react-best-practices",
        "description": "React and Next.js performance optimization guidelines from Vercel Engineering. This skill should be used when writing, reviewing, or refactoring React/Next.js code to ensure optimal performance patterns. Triggers on tasks involving React components, Next.js pages, data fetching, bundle optimization, or performance improvements.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\vercel-react-best-practices",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\vercel-react-best-practices\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [
          "on tasks involving React components",
          "Next.js pages",
          "data fetching",
          "bundle optimization",
          "or performance improvements."
        ],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "code"
          ],
          "primaryOperations": [],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.77,
          "profileSource": "rules",
          "secondaryArtifactHints": [],
          "secondaryOperationHints": [
            "automate",
            "guide"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "code": [
                "code"
              ]
            },
            "operationMatches": {},
            "classMatches": {
              "methodology_or_tutorial": [
                "guide"
              ],
              "integration_or_tooling": [
                "server"
              ]
            },
            "secondaryArtifacts": {},
            "secondaryOperations": {
              "automate": [
                "automated",
                "api"
              ],
              "guide": [
                "guide"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:6fac0b62e0407eac",
        "skillName": "code-review-excellence",
        "description": "Master effective code review practices to provide constructive feedback, catch bugs early, and foster knowledge sharing while maintaining team morale. Use when reviewing pull requests, establishing review standards, or mentoring developers.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\code-review-excellence",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\code-review-excellence\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_editor_or_analyzer",
          "primaryArtifactTypes": [
            "code"
          ],
          "primaryOperations": [
            "analyze"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.93,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "document"
          ],
          "secondaryOperationHints": [
            "create",
            "convert",
            "automate"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "code": [
                "code"
              ]
            },
            "operationMatches": {
              "analyze": [
                "review",
                "analysis"
              ]
            },
            "classMatches": {},
            "secondaryArtifacts": {
              "document": [
                "markdown"
              ]
            },
            "secondaryOperations": {
              "create": [
                "creating",
                "build",
                "make"
              ],
              "convert": [
                "transform"
              ],
              "automate": [
                "api"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:eed02e716f4e128e",
        "skillName": "docx",
        "description": "Comprehensive document creation, editing, and analysis with support for tracked changes, comments, formatting preservation, and text extraction. When Claude needs to work with professional documents (.docx files) for: (1) Creating new documents, (2) Modifying or editing content, (3) Working with tracked changes, (4) Adding comments, or any other document tasks",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\docx",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\docx\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "C:\\Users\\sunny\\.agents\\skills\\docx\\scripts",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [
          "scripts/",
          "scripts/__init__.py",
          "scripts/document.py",
          "scripts/templates/comments.xml",
          "scripts/templates/commentsExtended.xml",
          "scripts/templates/commentsExtensible.xml",
          "scripts/templates/commentsIds.xml",
          "scripts/templates/people.xml",
          "scripts/utilities.py"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "document"
          ],
          "primaryOperations": [
            "create",
            "analyze",
            "edit"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code",
            "image"
          ],
          "secondaryOperationHints": [
            "automate",
            "convert"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "document": [
                "docx",
                "document",
                ".docx",
                "word"
              ]
            },
            "operationMatches": {
              "create": [
                "creation",
                "creating",
                "create"
              ],
              "analyze": [
                "analysis",
                "analyze"
              ],
              "edit": [
                "editing",
                "edit"
              ]
            },
            "classMatches": {
              "workflow_or_script": [
                "workflow"
              ]
            },
            "secondaryArtifacts": {
              "code": [
                "scripts"
              ],
              "image": [
                "images"
              ]
            },
            "secondaryOperations": {
              "automate": [
                "workflow",
                "api"
              ],
              "convert": [
                "convert",
                "export"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:12bdda07b6e3d94d",
        "skillName": "pptx",
        "description": "Presentation creation, editing, and analysis. When Claude needs to work with presentations (.pptx files) for: (1) Creating new presentations, (2) Modifying or editing content, (3) Working with layouts, (4) Adding comments or speaker notes, or any other presentation tasks",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\pptx",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\pptx\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "C:\\Users\\sunny\\.agents\\skills\\pptx\\scripts",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [
          "scripts/",
          "scripts/html2pptx.js",
          "scripts/inventory.py",
          "scripts/rearrange.py",
          "scripts/remove_tables.py",
          "scripts/replace.py",
          "scripts/thumbnail.py"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "presentation"
          ],
          "primaryOperations": [
            "create",
            "analyze",
            "edit"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "document",
            "code",
            "image"
          ],
          "secondaryOperationHints": [
            "convert",
            "search",
            "automate"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "presentation": [
                "pptx",
                ".pptx",
                "presentation",
                "slide"
              ]
            },
            "operationMatches": {
              "create": [
                "creation",
                "creating",
                "create"
              ],
              "analyze": [
                "analysis",
                "analyze"
              ],
              "edit": [
                "editing",
                "edit"
              ]
            },
            "classMatches": {},
            "secondaryArtifacts": {
              "document": [
                "document",
                "markdown"
              ],
              "code": [
                "code",
                "script",
                "scripts"
              ],
              "image": [
                "images"
              ]
            },
            "secondaryOperations": {
              "convert": [
                "convert"
              ],
              "search": [
                "search",
                "find"
              ],
              "automate": [
                "workflow"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:c079e9663464857d",
        "skillName": "webnovel-write",
        "description": "Writes webnovel chapters (3000-5000 words). Use when the user asks to write a chapter or runs /webnovel-write. Runs context, drafting, review, polish, and data extraction.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\webnovel-write",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\webnovel-write\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "C:\\Users\\sunny\\.agents\\skills\\webnovel-write\\references",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [
          "references/",
          "references/core-constraints.md",
          "references/polish-guide.md",
          "references/style-adapter.md",
          "references/style-variants.md",
          "references/workflow-details.md",
          "references/writing/combat-scenes.md",
          "references/writing/desire-description.md",
          "references/writing/dialogue-writing.md",
          "references/writing/emotion-psychology.md",
          "references/writing/genre-hook-payoff-library.md",
          "references/writing/scene-description.md",
          "references/writing/typesetting.md"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "document"
          ],
          "primaryOperations": [
            "create",
            "edit",
            "analyze"
          ],
          "interactionMode": "workflow",
          "capabilityConfidence": 0.95,
          "profileSource": "llm_assisted",
          "secondaryArtifactHints": [
            "document",
            "code",
            "spreadsheet"
          ],
          "secondaryOperationHints": [
            "analyze",
            "create",
            "guide",
            "automate"
          ],
          "evidenceSignals": {
            "artifactMatches": {},
            "operationMatches": {},
            "classMatches": {},
            "secondaryArtifacts": {
              "document": [
                "md"
              ],
              "code": [
                "scripts"
              ],
              "spreadsheet": [
                "表格"
              ]
            },
            "secondaryOperations": {
              "analyze": [
                "review",
                "检查"
              ],
              "create": [
                "写",
                "产出"
              ],
              "guide": [
                "guide",
                "guidance"
              ],
              "automate": [
                "workflow"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:d783982503256036",
        "skillName": "xlsx",
        "description": "Comprehensive spreadsheet creation, editing, and analysis with support for formulas, formatting, data analysis, and visualization. When Claude needs to work with spreadsheets (.xlsx, .xlsm, .csv, .tsv, etc) for: (1) Creating new spreadsheets with formulas and formatting, (2) Reading or analyzing data, (3) Modify existing spreadsheets while preserving formulas, (4) Data analysis and visualization in spreadsheets, or (5) Recalculating formulas",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\xlsx",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\xlsx\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "spreadsheet"
          ],
          "primaryOperations": [
            "create",
            "edit"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.97,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code",
            "document"
          ],
          "secondaryOperationHints": [
            "analyze"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "spreadsheet": [
                "xlsx",
                "csv",
                ".xlsx",
                ".csv",
                "spreadsheet",
                "excel"
              ]
            },
            "operationMatches": {
              "create": [
                "creation",
                "creating"
              ],
              "edit": [
                "editing",
                "modify"
              ]
            },
            "classMatches": {},
            "secondaryArtifacts": {
              "code": [
                "coding",
                "script"
              ],
              "document": [
                "document"
              ]
            },
            "secondaryOperations": {
              "analyze": [
                "analysis",
                "analyze"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      }
    ],
    "skillEntries": [
      {
        "skillId": "global:c1a63ed2ed99a675",
        "skillName": "internal-comms",
        "description": "A set of resources to help me write all kinds of internal communications, using the formats that my company likes to use. Claude should use this skill whenever asked to write some sort of internal communications (status reports, leadership updates, 3P updates, company newsletters, FAQs, incident reports, project updates, etc.).",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\internal-comms",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\internal-comms\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "C:\\Users\\sunny\\.agents\\skills\\internal-comms\\examples",
        "availableFiles": [
          "examples/",
          "examples/3p-updates.md",
          "examples/company-newsletter.md",
          "examples/faq-answers.md",
          "examples/general-comms.md"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "methodology_or_tutorial",
          "primaryArtifactTypes": [
            "document"
          ],
          "primaryOperations": [
            "guide",
            "create"
          ],
          "interactionMode": "reference_guidance",
          "capabilityConfidence": 0.85,
          "profileSource": "llm_assisted",
          "secondaryArtifactHints": [
            "document"
          ],
          "secondaryOperationHints": [
            "guide",
            "edit"
          ],
          "evidenceSignals": {
            "artifactMatches": {},
            "operationMatches": {},
            "classMatches": {},
            "secondaryArtifacts": {
              "document": [
                "md"
              ]
            },
            "secondaryOperations": {
              "guide": [
                "how to"
              ],
              "edit": [
                "update"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "organization_leadership"
          ],
          "secondaryThemeTags": [],
          "themeConfidence": 0.6,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "organization_leadership": [
                "leadership"
              ]
            },
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:9bdbcd9561ed3ab7",
        "skillName": "find-skills",
        "description": "Helps users discover and install agent skills when they ask questions like \"how do I do X\", \"find a skill for X\", \"is there a skill that can...\", or express interest in extending capabilities. This skill should be used when the user is looking for functionality that might exist as an installable skill.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\find-skills",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\find-skills\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [],
        "keywords": [
          "how do I do X",
          "find a skill for X",
          "is there a skill that can..."
        ],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "skill_authoring",
          "primaryArtifactTypes": [
            "skill"
          ],
          "primaryOperations": [
            "search",
            "guide",
            "automate"
          ],
          "interactionMode": "reference_guidance",
          "capabilityConfidence": 0.85,
          "profileSource": "llm_assisted",
          "secondaryArtifactHints": [
            "document"
          ],
          "secondaryOperationHints": [
            "automate",
            "create",
            "analyze",
            "edit"
          ],
          "evidenceSignals": {
            "artifactMatches": {},
            "operationMatches": {
              "search": [
                "find",
                "search"
              ]
            },
            "classMatches": {
              "workflow_or_script": [
                "cli"
              ]
            },
            "secondaryArtifacts": {
              "document": [
                "document"
              ]
            },
            "secondaryOperations": {
              "automate": [
                "cli"
              ],
              "create": [
                "create",
                "creating",
                "make"
              ],
              "analyze": [
                "review"
              ],
              "edit": [
                "update"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "engineering_ai",
            "product_strategy"
          ],
          "secondaryThemeTags": [
            "specific_knowledge",
            "leverage",
            "organizational_design",
            "first_principles",
            "cognitive_bias"
          ],
          "themeConfidence": 0.85,
          "themeSource": "llm_assisted",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:c0f140bfdcd7e5cb",
        "skillName": "darwin-skill",
        "description": "Autonomous skill optimizer inspired by Karpathy's autoresearch. Evaluates SKILL.md files using an 8-dimension rubric (structure + effectiveness), runs hill-climbing with git version control, and validates improvements through test prompts. Use when user mentions \"优化skill\", \"skill评分\", \"自动优化\", \"auto optimize skills\", \"skill质量检查\", \"这个skill写得不好\", \"帮我改改skill\", \"skill怎么样\", \"提升skill质量\", \"skill review\", \"skill打分\".",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\darwin-skill",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\darwin-skill\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "C:\\Users\\sunny\\.agents\\skills\\darwin-skill\\assets",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [
          "assets/",
          "assets/aso-hero.png",
          "assets/banner-check.png",
          "assets/banner-en-check.png",
          "assets/banner-en.svg",
          "assets/banner.svg",
          "assets/chart-loop-en.html",
          "assets/chart-loop-en.png",
          "assets/chart-loop.html",
          "assets/chart-loop.png",
          "assets/chart-phases-en.html",
          "assets/chart-phases-en.png",
          "assets/chart-phases.html",
          "assets/chart-phases.png",
          "assets/chart-ratchet-en.html",
          "assets/chart-ratchet-en.png",
          "assets/chart-ratchet.html",
          "assets/chart-ratchet.png",
          "assets/chart-rubric-en.html",
          "assets/chart-rubric-en.png",
          "assets/chart-rubric.html",
          "assets/chart-rubric.png"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "skill_authoring",
          "primaryArtifactTypes": [
            "skill"
          ],
          "primaryOperations": [
            "analyze",
            "create"
          ],
          "interactionMode": "guided_workflow",
          "capabilityConfidence": 0.97,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code",
            "document",
            "presentation"
          ],
          "secondaryOperationHints": [
            "edit"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "skill": [
                "darwin-skill"
              ]
            },
            "operationMatches": {
              "analyze": [
                "review",
                "检查",
                "分析"
              ],
              "create": [
                "写"
              ]
            },
            "classMatches": {
              "skill_authoring": [
                "darwin-skill"
              ]
            },
            "secondaryArtifacts": {
              "code": [
                "scripts"
              ],
              "document": [
                "md"
              ],
              "presentation": [
                "slides"
              ]
            },
            "secondaryOperations": {
              "edit": [
                "编辑"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "engineering_ai",
            "product_strategy"
          ],
          "secondaryThemeTags": [
            "first_principles",
            "specific_knowledge",
            "organizational_design",
            "cognitive_bias",
            "leverage"
          ],
          "themeConfidence": 0.85,
          "themeSource": "llm_assisted",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:915a434eef063ad5",
        "skillName": "munger-perspective",
        "description": "查理·芒格的思维框架与表达方式。基于《穷查理宝典》、伯克希尔/Daily Journal股东会、\nUSC/哈佛演讲、访谈记录、外部批评等50+来源的深度调研，\n提炼5个核心心智模型、8条决策启发式和完整的表达DNA。\n用途：作为思维顾问，用芒格的视角分析问题、审视决策、提供反馈。\n当用户提到「用芒格的视角」「芒格会怎么看」「芒格模式」「munger perspective」时使用。\n也适用于：投资决策审视、认知偏误检查、跨学科思考训练、逆向思考练习。\n即使用户只是说「逆向思考一下」「这有什么认知偏误」「Lollapalooza效应」「能力圈之外」「激励结构是什么」也可触发。\n不要在用户只是问「这个决策靠谱吗」「帮我找盲点」等一般性问题时触发——只在涉及逆向思考、认知偏误、跨学科分析等芒格核心方法论时激活。",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\munger-perspective",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\munger-perspective\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "C:\\Users\\sunny\\.agents\\skills\\munger-perspective\\references",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "C:\\Users\\sunny\\.agents\\skills\\munger-perspective\\examples",
        "availableFiles": [
          "references/",
          "references/25-biases.md",
          "references/research.md",
          "references/查理芒格思想体系深度调研-20260404.md",
          "references/芒格表达风格DNA分析.md",
          "examples/",
          "examples/demo-conversation.md"
        ],
        "aliases": [],
        "triggers": [
          "逆向思考一下",
          "这有什么认知偏误",
          "Lollapalooza效应",
          "能力圈之外",
          "激励结构是什么",
          "这个决策靠谱吗",
          "帮我找盲点"
        ],
        "keywords": [
          "作为思维顾问",
          "用芒格的视角分析问题",
          "审视决策",
          "提供反馈。"
        ],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "advisor_or_perspective",
          "primaryArtifactTypes": [],
          "primaryOperations": [
            "advise"
          ],
          "interactionMode": "advisory",
          "capabilityConfidence": 0.73,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "document"
          ],
          "secondaryOperationHints": [
            "analyze",
            "search"
          ],
          "evidenceSignals": {
            "artifactMatches": {},
            "operationMatches": {
              "advise": [
                "perspective",
                "视角",
                "顾问"
              ]
            },
            "classMatches": {
              "advisor_or_perspective": [
                "perspective",
                "视角",
                "顾问",
                "思维框架"
              ],
              "methodology_or_tutorial": [
                "方法论"
              ]
            },
            "secondaryArtifacts": {
              "document": [
                "报告",
                "md"
              ]
            },
            "secondaryOperations": {
              "analyze": [
                "分析",
                "检查"
              ],
              "search": [
                "搜索"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "decision_quality"
          ],
          "secondaryThemeTags": [
            "cognitive_bias",
            "inversion"
          ],
          "themeConfidence": 0.84,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "decision_quality": [
                "认知偏误",
                "逆向思考",
                "思维框架"
              ]
            },
            "secondaryThemeMatches": {
              "cognitive_bias": [
                "认知偏误",
                "lollapalooza",
                "biases"
              ],
              "inversion": [
                "逆向思考"
              ]
            }
          }
        }
      },
      {
        "skillId": "global:beeb1ed76d4df463",
        "skillName": "elon-musk-perspective",
        "description": "马斯克的思维操作系统。基于传记、播客、推文、法庭证词、决策记录和外部批评的深度调研，\n提炼5个核心心智模型、8条决策启发式和完整的表达DNA。\n用途：作为思维顾问，用马斯克的视角分析问题、审视决策、拆解成本结构、挑战行业假设。\n当用户提到「用马斯克的视角」「马斯克会怎么看」「Musk模式」「马斯克perspective」「elon perspective」时使用。\n即使用户只是说「这个成本合理吗」「从第一性原理想想」「白痴指数是多少」「五步算法」「能不能垂直整合」也可触发。\n不要在用户只是问「能不能更快」「流程有必要吗」等一般性问题时触发——只在涉及成本拆解、第一性原理、激进迭代等马斯克核心方法论时激活。",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\elon-musk-perspective",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\elon-musk-perspective\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "C:\\Users\\sunny\\.agents\\skills\\elon-musk-perspective\\references",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "C:\\Users\\sunny\\.agents\\skills\\elon-musk-perspective\\examples",
        "availableFiles": [
          "references/",
          "references/Elon-Musk-思想体系调研-20260404.md",
          "references/research.md",
          "references/马斯克决策模式与行为分析-20260404.md",
          "references/马斯克即兴思考方式调研.md",
          "examples/",
          "examples/demo-conversation.md"
        ],
        "aliases": [],
        "triggers": [
          "这个成本合理吗",
          "从第一性原理想想",
          "白痴指数是多少",
          "五步算法",
          "能不能垂直整合",
          "能不能更快",
          "流程有必要吗"
        ],
        "keywords": [
          "作为思维顾问",
          "用马斯克的视角分析问题",
          "审视决策",
          "拆解成本结构",
          "挑战行业假设。"
        ],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "advisor_or_perspective",
          "primaryArtifactTypes": [],
          "primaryOperations": [
            "advise"
          ],
          "interactionMode": "advisory",
          "capabilityConfidence": 0.73,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "document"
          ],
          "secondaryOperationHints": [
            "analyze",
            "create",
            "search"
          ],
          "evidenceSignals": {
            "artifactMatches": {},
            "operationMatches": {
              "advise": [
                "perspective",
                "视角",
                "顾问"
              ]
            },
            "classMatches": {
              "advisor_or_perspective": [
                "perspective",
                "视角",
                "顾问",
                "思维框架"
              ],
              "methodology_or_tutorial": [
                "方法论"
              ]
            },
            "secondaryArtifacts": {
              "document": [
                "报告",
                "md"
              ]
            },
            "secondaryOperations": {
              "analyze": [
                "分析"
              ],
              "create": [
                "写"
              ],
              "search": [
                "搜索"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "product_strategy",
            "decision_quality"
          ],
          "secondaryThemeTags": [
            "cost_structure",
            "first_principles"
          ],
          "themeConfidence": 0.92,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "product_strategy": [
                "垂直整合",
                "成本结构"
              ],
              "decision_quality": [
                "第一性原理",
                "判断",
                "思维框架"
              ]
            },
            "secondaryThemeMatches": {
              "cost_structure": [
                "白痴指数",
                "成本结构"
              ],
              "first_principles": [
                "第一性原理"
              ]
            }
          }
        }
      },
      {
        "skillId": "global:15f18c5fcf5d256c",
        "skillName": "llm-video",
        "description": "Enterprise-grade AI video generation pipeline. Use this skill when the user wants to create educational videos, explain technical concepts, or generate visual presentations using code. The workflow separates 'Director' (Agent) from 'Engine' (Manim/FFmpeg).",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\llm-video",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\llm-video\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "C:\\Users\\sunny\\.agents\\skills\\llm-video\\references",
        "scriptsDir": "C:\\Users\\sunny\\.agents\\skills\\llm-video\\scripts",
        "assetsDir": "C:\\Users\\sunny\\.agents\\skills\\llm-video\\assets",
        "templatesDir": "",
        "examplesDir": "C:\\Users\\sunny\\.agents\\skills\\llm-video\\examples",
        "availableFiles": [
          "references/",
          "references/schema.md",
          "scripts/",
          "scripts/__pycache__/asset_manager.cpython-310.pyc",
          "scripts/__pycache__/compatibility.cpython-310.pyc",
          "scripts/__pycache__/layout_engine.cpython-310.pyc",
          "scripts/__pycache__/skins.cpython-310.pyc",
          "scripts/__pycache__/style_loader.cpython-310.pyc",
          "scripts/asset_manager.py",
          "scripts/compatibility.py",
          "scripts/components/__pycache__/cinematic.cpython-310.pyc",
          "scripts/components/__pycache__/code_window.cpython-310.pyc",
          "scripts/components/__pycache__/visual_debugger.cpython-310.pyc",
          "scripts/components/code_window.py",
          "scripts/components/visual_debugger.py",
          "scripts/engine.py",
          "scripts/layout_engine.py",
          "scripts/producer.py",
          "scripts/scout.py",
          "scripts/skins.py",
          "scripts/style_loader.py",
          "assets/",
          "assets/icon_brand_github.svg",
          "assets/icon_ui_user.svg",
          "assets/styles/default.json",
          "assets/styles/hacker_grid.json",
          "assets/test_icon.svg",
          "examples/",
          "examples/opencode_intro.json",
          "examples/skills_vs_mcp.json",
          "examples/test_flow.json"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "workflow_or_script",
          "primaryArtifactTypes": [
            "video"
          ],
          "primaryOperations": [
            "automate"
          ],
          "interactionMode": "workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code",
            "audio",
            "document"
          ],
          "secondaryOperationHints": [
            "create",
            "analyze",
            "convert",
            "search"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "video": [
                "video",
                "videos",
                "video generation",
                "manim"
              ]
            },
            "operationMatches": {
              "automate": [
                "workflow",
                "pipeline"
              ]
            },
            "classMatches": {
              "workflow_or_script": [
                "workflow",
                "pipeline"
              ]
            },
            "secondaryArtifacts": {
              "code": [
                "code",
                "script",
                "scripts"
              ],
              "audio": [
                "voice"
              ],
              "document": [
                "md"
              ]
            },
            "secondaryOperations": {
              "create": [
                "create",
                "generate",
                "generated"
              ],
              "analyze": [
                "analyze"
              ],
              "convert": [
                "convert"
              ],
              "search": [
                "search"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "engineering_ai"
          ],
          "secondaryThemeTags": [],
          "themeConfidence": 0.58,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "engineering_ai": [
                "llm"
              ]
            },
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:67cb9ebfa7543040",
        "skillName": "huashu-nuwa",
        "description": "女娲造人：输入人名/主题/甚至只是模糊需求，自动深度调研→思维框架提炼→生成可运行的人物Skill。\n两种入口：(1)明确人名→直接蒸馏 (2)模糊需求→诊断推荐→再蒸馏。\n触发词：「造skill」「蒸馏XX」「女娲」「造人」「XX的思维方式」「做个XX视角」「更新XX的skill」。\n模糊需求也触发：「我想提升决策质量」「有没有一种思维方式能帮我...」「我需要一个思维顾问」。",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\huashu-nuwa",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\huashu-nuwa\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "C:\\Users\\sunny\\.agents\\skills\\huashu-nuwa\\references",
        "scriptsDir": "C:\\Users\\sunny\\.agents\\skills\\huashu-nuwa\\scripts",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "C:\\Users\\sunny\\.agents\\skills\\huashu-nuwa\\examples",
        "availableFiles": [
          "references/",
          "references/extraction-framework.md",
          "references/skill-template.md",
          "scripts/",
          "scripts/download_subtitles.sh",
          "scripts/merge_research.py",
          "scripts/quality_check.py",
          "scripts/srt_to_transcript.py",
          "examples/",
          "examples/andrej-karpathy-perspective/references/research/01-writings.md",
          "examples/andrej-karpathy-perspective/references/research/02-conversations.md",
          "examples/andrej-karpathy-perspective/references/research/03-expression-dna.md",
          "examples/andrej-karpathy-perspective/references/research/04-external-views.md",
          "examples/andrej-karpathy-perspective/references/research/05-decisions.md",
          "examples/andrej-karpathy-perspective/references/research/06-timeline.md",
          "examples/andrej-karpathy-perspective/SKILL.md",
          "examples/elon-musk-perspective/references/Elon-Musk-思想体系调研-20260404.md",
          "examples/elon-musk-perspective/references/research.md",
          "examples/elon-musk-perspective/references/马斯克决策模式与行为分析-20260404.md",
          "examples/elon-musk-perspective/references/马斯克即兴思考方式调研.md",
          "examples/elon-musk-perspective/SKILL.md",
          "examples/feynman-perspective/references/research.md",
          "examples/feynman-perspective/references/费曼外部评价调研.md",
          "examples/feynman-perspective/references/费曼著作与系统思考调研-20260404.md",
          "examples/feynman-perspective/references/费曼表达风格调研.md",
          "examples/feynman-perspective/references/费曼重大决策调研-20260404.md",
          "examples/feynman-perspective/references/费曼长对话与即兴思考方式调研-20260404.md",
          "examples/feynman-perspective/SKILL.md",
          "examples/ilya-sutskever-perspective/references/research/01-writings.md",
          "examples/ilya-sutskever-perspective/references/research/02-conversations.md",
          "examples/ilya-sutskever-perspective/references/research/03-expression-dna.md",
          "examples/ilya-sutskever-perspective/references/research/04-external-views.md",
          "examples/ilya-sutskever-perspective/references/research/05-decisions.md",
          "examples/ilya-sutskever-perspective/references/research/06-timeline.md",
          "examples/ilya-sutskever-perspective/SKILL.md",
          "examples/mrbeast-perspective/references/research/02-conversations.md",
          "examples/mrbeast-perspective/references/research/03-expression-dna.md",
          "examples/mrbeast-perspective/references/research/04-external-views.md",
          "examples/mrbeast-perspective/references/research/05-decisions.md",
          "examples/mrbeast-perspective/references/research/06-timeline.md",
          "examples/mrbeast-perspective/scripts/analyze_titles.py",
          "examples/mrbeast-perspective/scripts/fetch_youtube_subtitles.sh",
          "examples/mrbeast-perspective/scripts/retention_curve_checker.py",
          "examples/mrbeast-perspective/scripts/thumbnail_audit.py",
          "examples/mrbeast-perspective/SKILL.md",
          "examples/munger-perspective/references/25-biases.md",
          "examples/munger-perspective/references/research.md",
          "examples/munger-perspective/references/查理芒格思想体系深度调研-20260404.md",
          "examples/munger-perspective/references/芒格表达风格DNA分析.md",
          "examples/munger-perspective/SKILL.md",
          "examples/naval-perspective/references/naval-agent2-conversations.md",
          "examples/naval-perspective/references/naval-agent3-expression-dna.md",
          "examples/naval-perspective/references/naval-ravikant-agent1-著作与系统思考.md",
          "examples/naval-perspective/references/quality-validation.md",
          "examples/naval-perspective/SKILL.md",
          "examples/paul-graham-perspective/references/research/01-writings.md",
          "examples/paul-graham-perspective/references/research/02-conversations.md",
          "examples/paul-graham-perspective/references/research/03-expression-dna.md",
          "examples/paul-graham-perspective/references/research/04-external-views.md",
          "examples/paul-graham-perspective/references/research/05-decisions.md",
          "examples/paul-graham-perspective/references/research/06-timeline.md",
          "examples/paul-graham-perspective/SKILL.md",
          "examples/steve-jobs-perspective/references/demo-conversation-2026-04-05.md",
          "examples/steve-jobs-perspective/references/research/01-writings.md",
          "examples/steve-jobs-perspective/references/research/02-conversations.md",
          "examples/steve-jobs-perspective/references/research/03-expression-dna.md",
          "examples/steve-jobs-perspective/references/research/04-external-views.md",
          "examples/steve-jobs-perspective/references/research/05-decisions.md",
          "examples/steve-jobs-perspective/references/research/06-timeline.md",
          "examples/steve-jobs-perspective/SKILL.md",
          "examples/sun-yuchen-perspective/README.md",
          "examples/sun-yuchen-perspective/references/research/01-writings.md",
          "examples/sun-yuchen-perspective/references/research/02-conversations.md",
          "examples/sun-yuchen-perspective/references/research/03-expression-dna.md",
          "examples/sun-yuchen-perspective/references/research/04-external-views.md",
          "examples/sun-yuchen-perspective/references/research/05-decisions.md",
          "examples/sun-yuchen-perspective/references/research/06-timeline.md",
          "examples/sun-yuchen-perspective/SKILL.md",
          "examples/taleb-perspective/references/research.md",
          "examples/taleb-perspective/references/塔勒布外部批评调研.md",
          "examples/taleb-perspective/references/塔勒布思想体系调研.md",
          "examples/taleb-perspective/references/塔勒布深度对话调研.md",
          "examples/taleb-perspective/references/塔勒布碎片表达与社交媒体人格调研.md",
          "examples/taleb-perspective/references/塔勒布重大决策与实际行动调研-20260404.md",
          "examples/taleb-perspective/SKILL.md",
          "examples/trump-perspective/references/research/01-writings.md",
          "examples/trump-perspective/references/research/02-conversations.md",
          "examples/trump-perspective/references/research/03-expression-dna.md",
          "examples/trump-perspective/references/research/04-external-views.md",
          "examples/trump-perspective/references/research/05-decisions.md",
          "examples/trump-perspective/references/research/06-timeline.md",
          "examples/trump-perspective/SKILL.md",
          "examples/x-mastery-mentor/references/algorithm-niche.md",
          "examples/x-mastery-mentor/references/growth-monetization.md",
          "examples/x-mastery-mentor/references/mental-models-heuristics.md",
          "examples/x-mastery-mentor/references/quality-analytics.md",
          "examples/x-mastery-mentor/references/research/01-writing-methods.md",
          "examples/x-mastery-mentor/references/research/02-growth-engines.md",
          "examples/x-mastery-mentor/references/research/03-content-brand.md",
          "examples/x-mastery-mentor/references/research/04-platform-mechanics.md",
          "examples/x-mastery-mentor/references/research/05-ai-tech-niche.md",
          "examples/x-mastery-mentor/references/research/06-cases-antipatterns.md",
          "examples/x-mastery-mentor/references/writing-workshop.md",
          "examples/x-mastery-mentor/SKILL.md",
          "examples/zhang-yiming-perspective/references/research/01-writings.md",
          "examples/zhang-yiming-perspective/references/research/02-conversations.md",
          "examples/zhang-yiming-perspective/references/research/03-expression-dna.md",
          "examples/zhang-yiming-perspective/references/research/04-external-views.md",
          "examples/zhang-yiming-perspective/references/research/05-decisions.md",
          "examples/zhang-yiming-perspective/references/research/06-timeline.md",
          "examples/zhang-yiming-perspective/SKILL.md",
          "examples/zhangxuefeng-perspective/references/research/01-writings.md",
          "examples/zhangxuefeng-perspective/references/research/02-conversations.md",
          "examples/zhangxuefeng-perspective/references/research/03-expression-dna.md",
          "examples/zhangxuefeng-perspective/references/research/04-external-views.md",
          "examples/zhangxuefeng-perspective/references/research/05-decisions.md",
          "examples/zhangxuefeng-perspective/references/research/06-timeline.md",
          "examples/zhangxuefeng-perspective/SKILL.md"
        ],
        "aliases": [],
        "triggers": [
          "造skill",
          "蒸馏XX",
          "女娲",
          "造人",
          "XX的思维方式",
          "做个XX视角",
          "更新XX的skill",
          "我想提升决策质量",
          "有没有一种思维方式能帮我...",
          "我需要一个思维顾问"
        ],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "skill_authoring",
          "primaryArtifactTypes": [
            "skill"
          ],
          "primaryOperations": [
            "create",
            "advise"
          ],
          "interactionMode": "guided_workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code",
            "document",
            "pdf",
            "video"
          ],
          "secondaryOperationHints": [
            "create",
            "analyze",
            "convert",
            "edit"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "skill": [
                "nuwa",
                "女娲",
                "造skill",
                "造人",
                "蒸馏",
                "女娲造人",
                "人物skill"
              ]
            },
            "operationMatches": {
              "create": [
                "生成",
                "写"
              ],
              "advise": [
                "视角",
                "顾问"
              ]
            },
            "classMatches": {
              "skill_authoring": [
                "nuwa",
                "女娲",
                "造skill",
                "造人",
                "蒸馏",
                "女娲造人",
                "人物skill"
              ],
              "advisor_or_perspective": [
                "视角",
                "顾问",
                "思维框架"
              ]
            },
            "secondaryArtifacts": {
              "code": [
                "scripts",
                "脚本"
              ],
              "document": [
                "md",
                "文章"
              ],
              "pdf": [
                "pdf"
              ],
              "video": [
                "视频"
              ]
            },
            "secondaryOperations": {
              "create": [
                "生成",
                "写",
                "创建"
              ],
              "analyze": [
                "检查",
                "分析",
                "analyze",
                "audit"
              ],
              "convert": [
                "导出"
              ],
              "edit": [
                "调整"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "decision_quality"
          ],
          "secondaryThemeTags": [],
          "themeConfidence": 0.66,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "decision_quality": [
                "决策质量",
                "思维框架",
                "判断"
              ]
            },
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:ed7024257d0b0f51",
        "skillName": "huashu-speech-coach",
        "description": "演讲与分享教练。基于Patrick Winston（MIT AI教授）的How to Speak方法论，帮助准备线下培训、技术分享、B站教程视频等演讲场景。当用户提到\"演讲\"、\"分享\"、\"培训\"、\"讲课\"、\"PPT演讲\"、\"开场\"、\"结尾\"、\"如何讲\"、\"演讲结构\"时使用此技能。",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\huashu-speech-coach",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\huashu-speech-coach\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "C:\\Users\\sunny\\.agents\\skills\\huashu-speech-coach\\references",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [
          "references/",
          "references/patrick-winston-how-to-speak.md"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "methodology_or_tutorial",
          "primaryArtifactTypes": [],
          "primaryOperations": [
            "guide"
          ],
          "interactionMode": "reference_guidance",
          "capabilityConfidence": 0.62,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "presentation",
            "video",
            "document",
            "code"
          ],
          "secondaryOperationHints": [
            "analyze",
            "advise",
            "create",
            "edit"
          ],
          "evidenceSignals": {
            "artifactMatches": {},
            "operationMatches": {
              "guide": [
                "how to",
                "教程"
              ]
            },
            "classMatches": {
              "methodology_or_tutorial": [
                "教程",
                "方法论"
              ],
              "advisor_or_perspective": [
                "视角"
              ],
              "workflow_or_script": [
                "脚本"
              ]
            },
            "secondaryArtifacts": {
              "presentation": [
                "ppt",
                "presentation",
                "幻灯片"
              ],
              "video": [
                "视频"
              ],
              "document": [
                "md"
              ],
              "code": [
                "脚本"
              ]
            },
            "secondaryOperations": {
              "analyze": [
                "检查",
                "分析"
              ],
              "advise": [
                "视角"
              ],
              "create": [
                "build",
                "写"
              ],
              "edit": [
                "调整"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "content_media"
          ],
          "secondaryThemeTags": [],
          "themeConfidence": 0.66,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "content_media": [
                "视频",
                "youtube"
              ]
            },
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:f45c1cf2ca76d568",
        "skillName": "doc-coauthoring",
        "description": "Guide users through a structured workflow for co-authoring documentation. Use when user wants to write documentation, proposals, technical specs, decision docs, or similar structured content. This workflow helps users efficiently transfer context, refine content through iteration, and verify the doc works for readers. Trigger when user mentions writing docs, creating proposals, drafting specs, or similar documentation tasks.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\doc-coauthoring",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\doc-coauthoring\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [
          "Guide users through a structured workflow for co-authoring documentation. Use when user wants to write documentation",
          "proposals",
          "technical specs",
          "decision docs",
          "or similar structured content. This workflow helps users efficiently transfer context",
          "refine content through iteration",
          "and verify the doc works for readers. Trigger when user mentions writing docs",
          "creating proposals",
          "drafting specs",
          "or similar documentation tasks."
        ],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "document"
          ],
          "primaryOperations": [
            "create",
            "automate",
            "guide"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "image"
          ],
          "secondaryOperationHints": [
            "edit",
            "advise"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "document": [
                "doc",
                "document"
              ]
            },
            "operationMatches": {
              "create": [
                "creating",
                "create",
                "creation",
                "build",
                "draft"
              ],
              "automate": [
                "workflow"
              ],
              "guide": [
                "guide"
              ]
            },
            "classMatches": {
              "methodology_or_tutorial": [
                "guide"
              ],
              "workflow_or_script": [
                "workflow"
              ]
            },
            "secondaryArtifacts": {
              "image": [
                "image",
                "images"
              ]
            },
            "secondaryOperations": {
              "edit": [
                "editing"
              ],
              "advise": [
                "advise"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "content_media",
            "writing_communication"
          ],
          "secondaryThemeTags": [],
          "themeConfidence": 0.6,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "content_media": [
                "content"
              ],
              "writing_communication": [
                "writing"
              ]
            },
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:2acae9709d3c1c02",
        "skillName": "vercel-react-best-practices",
        "description": "React and Next.js performance optimization guidelines from Vercel Engineering. This skill should be used when writing, reviewing, or refactoring React/Next.js code to ensure optimal performance patterns. Triggers on tasks involving React components, Next.js pages, data fetching, bundle optimization, or performance improvements.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\vercel-react-best-practices",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\vercel-react-best-practices\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "",
        "scriptsDir": "",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [],
        "aliases": [],
        "triggers": [
          "on tasks involving React components",
          "Next.js pages",
          "data fetching",
          "bundle optimization",
          "or performance improvements."
        ],
        "keywords": [],
        "tags": [],
        "directCanonicalFamilies": [],
        "canonicalFamilies": [],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "code"
          ],
          "primaryOperations": [],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.77,
          "profileSource": "rules",
          "secondaryArtifactHints": [],
          "secondaryOperationHints": [
            "automate",
            "guide"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "code": [
                "code"
              ]
            },
            "operationMatches": {},
            "classMatches": {
              "methodology_or_tutorial": [
                "guide"
              ],
              "integration_or_tooling": [
                "server"
              ]
            },
            "secondaryArtifacts": {},
            "secondaryOperations": {
              "automate": [
                "automated",
                "api"
              ],
              "guide": [
                "guide"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [],
          "secondaryThemeTags": [],
          "themeConfidence": 0.1,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {},
            "secondaryThemeMatches": {}
          }
        }
      }
    ],
    "skillRootDescriptors": [
      {
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "sourceType": "global",
        "workspacePath": "",
        "workspaceId": null,
        "projectId": null,
        "visibility": "global"
      },
      {
        "rootPath": "E:\\Projects\\test1\\.agents\\skills",
        "sourceType": "scoped_workspace",
        "workspacePath": "E:\\Projects\\test1",
        "workspaceId": "test1",
        "projectId": "test1",
        "visibility": "scoped"
      }
    ],
    "mcpTools": [],
    "mcpStage1Servers": [],
    "mcpServers": [],
    "mcpFamilies": [],
    "pluginHostTools": [],
    "pluginHostSelectedFamilies": [],
    "seedUnit": "skill_or_mcp_server",
    "skillCandidates": 10,
    "mcpCandidates": 0,
    "mcpServerCandidates": 0,
    "pluginHostCandidates": 0,
    "skillInventoryCount": 37,
    "skillPoolSize": 37,
    "skillStage1HitCount": 19,
    "skillStage1ShortlistCount": 10,
    "skillLexicalPoolSize": 10,
    "skillFinalExposedCount": 10,
    "mcpInventoryCount": 0,
    "mcpPoolSize": 0,
    "mcpStage1HitCount": 0,
    "mcpStage1ShortlistCount": 0,
    "mcpLexicalPoolSize": 0,
    "mcpFinalExposedCount": 0,
    "mcpServerPoolSize": 0,
    "mcpServerCount": 0,
    "mcpFamilyPoolSize": 0,
    "mcpFamilyCount": 0,
    "mcpExpandedToolCount": 0,
    "mcpSelectedServers": [],
    "mcpSelectedFamilies": [],
    "pluginHostPoolSize": 0,
    "requestedSkillLimit": 5,
    "requestedMcpLimit": 6,
    "requestedPluginHostLimit": 6,
    "effectiveSkillLimit": 10,
    "effectiveMcpLimit": 0,
    "effectivePluginHostLimit": 6,
    "crossRuntimeEscape": true,
    "pluginHostSeedCount": 0,
    "pluginHostBoundLimit": 12,
    "pluginHostBoundCount": 0,
    "totalInstalledSkills": 37,
    "totalConnectedMcpTools": 0,
    "totalPluginHostTools": 0,
    "agentCount": 0
  },
  "plannerPlanId": "planner-project-stress"
}
```

## 模块解析摘要
| 模块 | 出现 | Estimated Tokens | 备注 |
|---|---:|---:|---|
| base prompt / system persona | Y | 541 |  |
| runtime registry / capability registry | N | 0 |  |
| specialist agent registry | N | 0 |  |
| direct tool registry | N | 0 |  |
| [NETWORK SUPERVISOR CONTEXT] | N | 0 | subagent 正常情况下不应继承 supervisor 级 network card。 |
| [EXTERNAL APP INSTRUCTIONS] | N | 0 |  |
| engineering context | Y | 553 |  |
| planner context / delegated task plan | Y | 548 |  |
| artifact awareness | N | 0 |  |
| todos / active task plan | Y | 124 |  |
| memory profile | N | 0 |  |
| memory summary | N | 0 |  |
| memory map | N | 0 |  |
| workflow hints | N | 0 |  |
| workspace rules | N | 0 |  |
| environment | Y | 114 |  |
| extensions runtime route block | Y | 898 |  |
| interactive CLI rule | Y | 141 |  |
| group moderation / execution hints | N | 0 |  |

## SYSTEM_CONTENT 全文
```text
<system_persona>
You are a specialized agent named Implementation Engineer.
You are Implementation Engineer, a V8 Agent OS specialist subagent.

Shared V8 subagent discipline:
- Start from the delegated task brief, not the whole supervisor conversation. Restate only the assumptions that affect your slice.
- Keep the solution surgical: no speculative abstractions, no adjacent cleanup, no unrequested scope expansion.
- Preserve runtime boundaries. Subagents do not have ComputerUse, RPA, or Memory runtime authority by default; ask the supervisor to route those actions.
- Define evidence before claiming completion. Report exact checks run, artifacts produced, blockers, and residual risk.
- Return compact, aggregatable output for the supervisor. Local self-check is not final acceptance.

Mission:
- Implement bounded code changes with minimal, reviewable diffs.
- Preserve V8 runtime contracts, event flow, config truth, and compatibility shells unless the task brief explicitly changes them.

Input contract:
- A delegated implementation task with scoped files or modules, acceptance criteria, and any known risks.
- Existing code context discovered through read/search tools and route-selected extensions.

Operating protocol:
- Inspect before editing. Identify the smallest viable patch.
- Use existing patterns and types before introducing new abstractions.
- Keep implementation and verification coupled: each behavior change needs a check, diagnostic, or explicit residual risk.
- If the requested change crosses runtime boundaries, stop and report the boundary instead of improvising a second architecture.

Output contract:
- Changed behavior in 3-6 bullets.
- Files touched and why.
- Verification command/results or exact reason verification could not run.
- Any compatibility or migration note for the supervisor.

Verification contract:
- Prefer targeted tests or compile/type checks. If not runnable, provide a deterministic inspection checklist and name the gap.

Boundaries and refusal rules:
- Do not refactor unrelated code, reformat large files, or clean old dead code unless the task owns it.
- Do not execute desktop/RPA/memory operations.
- Do not claim final user acceptance; provide local self-check only.

Final response shape:
1. Result summary.
2. Evidence and artifacts.
3. Risks, blockers, or handoff notes.
4. Local self-check status.

Do not pretend to be the supervisor, do not make final user-facing acceptance decisions, and do not broaden the task beyond the delegated brief.
</system_persona>

<environment>
OS: Windows
Current Time: 2026-04-24T03:29:13Z
Local Workspace Absolute Path: C:\Users\sunny\.v8-agent-os\workspace
When generating visual artifacts, media, or formal reports meant to be viewed in the Web UI, you MUST save them to the Local Workspace above.
Do NOT expose raw local filesystem paths, raw /api/workspace/files links, or raw <img>/<video>/<audio> HTML in the final reply. Reference generated media naturally in prose and rely on the runtime artifact/resource pipeline for rendering.
</environment>

<active_task_plan>
Task Name: Engineering Stress Validation
Supervisor has delegated a portion of this grand plan to you.
Here is the context of the overall plan and current progress:

<plan_details>
1. 收敛 scoped inventory stale barrier
2. 验证 visible registry cache
3. 核对 proof/workflow 不受污染
</plan_details>

<current_progress>
  [→] #0: 定位 visible roots 与 route cache 边界
  [ ] #1: 确认真实启动入口 stale barrier 语义
  [ ] #2: 完成回归与排毒结论
</current_progress>
</active_task_plan>

<delegated_task_plan>
You are executing one bounded task from the supervisor's planner/delegation pipeline.
Use this local task contract as the routing truth; do not reinterpret the original user request as your primary scope.
Plan ID: planner-project-stress
Execution Strategy: mixed
Plan Summary: 先定位 scoped cache 与 visible roots 的一致性边界，再做最小修复与验证。
Task Count: 1
Risk Flags: scoped_extensions, cache_invalidation, engineering_lane
Global Acceptance Contract: 保留 runtime-governance 纪律，不扩大兼容层污染。

Assigned Task Brief:
- Task Brief ID: project-cache-fix
- Goal: 修复项目级工作区 extensions 预筛 stale shortlist 与 scoped cache 失效问题，并补足回归验证。
- Context: 当前项目级 skill 发现、visible registry 与 route cache 之间存在主链一致性风险，需要围绕工程证据做最小修复。
- Write Set: apps/v8-agent-os-engine/runtimes/extensions/runtime.py, apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py, apps/v8-agent-os-engine/tests/test_extensions_prefilter_selection.py
- Behavior Scope: code_edit, verification, runtime_governance
- Required Capabilities: software_engineering, implementation, testing
- Execution Lane Hint: engineering
- Acceptance Contract: 给出最小修复、回归测试覆盖和明确验证结论，不扩大 runtime 语义面。

Engineering Task Capsule:
- Engineering Role: implementation
- Critical Files: apps/v8-agent-os-engine/runtimes/extensions/runtime.py, apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py
- Read Set: apps/v8-agent-os-engine/runtimes/extensions/runtime.py, apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py, apps/v8-agent-os-engine/tests/test_extensions_prefilter_selection.py
- Write Set: apps/v8-agent-os-engine/runtimes/extensions/runtime.py, apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py, apps/v8-agent-os-engine/tests/test_extensions_prefilter_selection.py
- Verification Contract: pytest apps/v8-agent-os-engine/tests/test_extensions_prefilter_selection.py -q, py_compile runtime.py / loader.py
- Proof Expectations: diff, verification, residual_risk
- Risk Flags: scoped_workspace, cache_invalidation, runtime_consistency
</delegated_task_plan>

[Extensions Runtime]
- Skills 候选：10 / 已安装 37
- MCP 工具候选：0 / 已连接工具 0
- Cross-runtime escape：已启用。检测到阻塞/切换类任务语义，本轮适度放宽跨 runtime 候选。
- 候选预筛：当前使用第 1 层 shortlist。
- 当前命中的 Skills 目录入口：
  - internal-comms [global]
    - Skill description: A set of resources to help me write all kinds of internal communications, using the formats that my company likes to use. Claude should use this skill whenever asked to write so...
  - find-skills [global]
    - Skill description: Helps users discover and install agent skills when they ask questions like "how do I do X", "find a skill for X", "is there a skill that can...", or express interest in extendin...
  - darwin-skill [global]
    - Skill description: Autonomous skill optimizer inspired by Karpathy's autoresearch. Evaluates SKILL.md files using an 8-dimension rubric (structure + effectiveness), runs hill-climbing with git ver...
  - munger-perspective [global]
    - Skill description: 查理·芒格的思维框架与表达方式。基于《穷查理宝典》、伯克希尔/Daily Journal股东会、 USC/哈佛演讲、访谈记录、外部批评等50+来源的深度调研， 提炼5个核心心智模型、8条决策启发式和完整的表达DNA。 用途：作为思维顾问，用芒格的视角分析问题、审视决策、提供反馈。 当用户提到「用芒格的视角」「芒格会怎么看」「芒格模式」「munger p...
  - elon-musk-perspective [global]
    - Skill description: 马斯克的思维操作系统。基于传记、播客、推文、法庭证词、决策记录和外部批评的深度调研， 提炼5个核心心智模型、8条决策启发式和完整的表达DNA。 用途：作为思维顾问，用马斯克的视角分析问题、审视决策、拆解成本结构、挑战行业假设。 当用户提到「用马斯克的视角」「马斯克会怎么看」「Musk模式」「马斯克perspective」「elon perspectiv...
  - llm-video [global]
    - Skill description: Enterprise-grade AI video generation pipeline. Use this skill when the user wants to create educational videos, explain technical concepts, or generate visual presentations usin...
  - huashu-nuwa [global]
    - Skill description: 女娲造人：输入人名/主题/甚至只是模糊需求，自动深度调研→思维框架提炼→生成可运行的人物Skill。 两种入口：(1)明确人名→直接蒸馏 (2)模糊需求→诊断推荐→再蒸馏。 触发词：「造skill」「蒸馏XX」「女娲」「造人」「XX的思维方式」「做个XX视角」「更新XX的skill」。 模糊需求也触发：「我想提升决策质量」「有没有一种思维方式能帮我.....
  - huashu-speech-coach [global]
    - Skill description: 演讲与分享教练。基于Patrick Winston（MIT AI教授）的How to Speak方法论，帮助准备线下培训、技术分享、B站教程视频等演讲场景。当用户提到"演讲"、"分享"、"培训"、"讲课"、"PPT演讲"、"开场"、"结尾"、"如何讲"、"演讲结构"时使用此技能。
  - doc-coauthoring [global]
    - Skill description: Guide users through a structured workflow for co-authoring documentation. Use when user wants to write documentation, proposals, technical specs, decision docs, or similar struc...
  - vercel-react-best-practices [global]
    - Skill description: React and Next.js performance optimization guidelines from Vercel Engineering. This skill should be used when writing, reviewing, or refactoring React/Next.js code to ensure opt...
[/Extensions Runtime]

[Interactive CLI Rule]
Use `run_system_command` only for short synchronous commands.
Use `command_session_broker(mode=start)` for long-running commands, interactive CLIs/REPLs, and dev servers; continue with `observe`, `input`, or `terminate`.
Treat broker JSON (`summary`, `recommendedNextAction`, `awaitingInput`, `hasMore`, `status`) as the primary truth; use `debug=true` only for raw terminal diagnostics.
If terminal automation or observation is uncertain, report that uncertainty instead of inventing progress.

When you have fully completed your assigned task, respond with your findings or status to return control to the supervisor.
```
