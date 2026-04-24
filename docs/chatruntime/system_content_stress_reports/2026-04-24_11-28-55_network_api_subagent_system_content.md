# Network API / subagent SYSTEM_CONTENT 快照

## 场景配置摘要
```json
{
  "transport": "network_supervisor_openai",
  "scope": "workspace_less_external_thread",
  "engineeringMode": "off",
  "sessionId": "stress-network-2026-04-24_11-28-55",
  "runId": "run-network-2026-04-24_11-28-55",
  "currentScope": "global",
  "scopeChain": [
    "global"
  ],
  "historyTurns": 4
}
```

## 路由与诊断摘要
```json
{
  "selectedAgent": {
    "id": "research-synthesizer",
    "name": "Research Synthesizer",
    "toolMode": "contextual_auto"
  },
  "selectionDiagnostics": {
    "selectionReason": "strong_capability_match",
    "selectionConfidence": 1.0,
    "matchSignals": [
      "artifact:analysis",
      "behavior:summarize",
      "agentClass:researcher",
      "plannerSuitability:low",
      "engineeringRole:review",
      "lexical:12"
    ],
    "targetId": "research-synthesizer"
  },
  "route": {
    "mode": "stage1_only",
    "skillInventoryRevision": "9886aa049a8051d103d94500f01989faa3255505",
    "visibleRootSignature": "d50eb1ba62f62f9c759be61ce33008212c6cafe6",
    "visibleRootRevisionKey": "29390707281e230bbf61e02bdd85dcc5cbd49cc9",
    "visibleRegistryCacheHit": true,
    "inventoryReadyState": "ready",
    "snapshotFreshness": "live",
    "inventoryBarrierApplied": true,
    "inventoryBarrierWaitMs": 0.0,
    "inventoryBarrierTimedOut": false,
    "dirtyVisibleRoots": [],
    "changedRoots": [],
    "scopedRefreshMode": null,
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
      "ai-video-generation",
      "ai-avatar-video",
      "huashu-nuwa",
      "docx",
      "pptx",
      "skill-creator",
      "xlsx",
      "seedance-prompt-en",
      "frontend-design",
      "wechat-article-writer"
    ],
    "selectedSkillIds": [
      "global:21909ae93fe53f6c",
      "global:00f913d69525ab2a",
      "global:67cb9ebfa7543040",
      "global:eed02e716f4e128e",
      "global:12bdda07b6e3d94d",
      "global:ea79d371a63649a1",
      "global:d783982503256036",
      "global:ffaa3a8ba976d59a",
      "global:b2fdc7c6c24cc4b2",
      "global:f2d26475edec2f15"
    ],
    "artifactIntent": null,
    "documentSubIntent": null,
    "operationIntent": "create",
    "directCanonicalFamilies": [],
    "canonicalFamilies": [],
    "primaryCanonicalFamily": null,
    "shortCanonicalNarrowing": false,
    "shortCanonicalNarrowingApplied": false,
    "primaryThemeIntents": [
      "writing_communication"
    ],
    "secondaryThemeHints": [],
    "rankingSignals": {
      "artifactAnchor": false,
      "documentSubIntent": null,
      "operationIntent": true,
      "topicTokenCount": 42
    },
    "themeRankingSignals": {
      "themeIntent": true,
      "secondaryThemeHints": 0,
      "artifactAnchorPresent": false,
      "fallbackInjectedCount": 0
    },
    "profileMatchedCount": 10,
    "profileBackfilledCount": 0,
    "themeMatchedCount": 5,
    "themeBackfilledCount": 0,
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
        "skillId": "global:21909ae93fe53f6c",
        "skillName": "ai-video-generation",
        "description": "Generate AI videos with Google Veo, Seedance, Wan, Grok and 40+ models via inference.sh CLI. Models: Veo 3.1, Veo 3, Seedance 1.5 Pro, Wan 2.5, Grok Imagine Video, OmniHuman, Fabric, HunyuanVideo. Capabilities: text-to-video, image-to-video, lipsync, avatar animation, video upscaling, foley sound. Use for: social media videos, marketing content, explainer videos, product demos, AI avatars. Triggers: video generation, ai video, text to video, image to video, veo, animate image, video from image, ai animation, video generator, generate video, t2v, i2v, ai video maker, create video with ai, runway alternative, pika alternative, sora alternative, kling alternative",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\ai-video-generation",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\ai-video-generation\\SKILL.md",
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
          "video generation",
          "ai video",
          "text to video",
          "image to video",
          "veo",
          "animate image",
          "video from image",
          "ai animation",
          "video generator",
          "generate video",
          "t2v",
          "i2v",
          "ai video maker",
          "create video with ai",
          "runway alternative",
          "pika alternative",
          "sora alternative",
          "kling alternative"
        ],
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
            "create"
          ],
          "interactionMode": "workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "image",
            "audio"
          ],
          "secondaryOperationHints": [
            "automate"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "video": [
                "video",
                "video generation",
                "videos",
                "text-to-video",
                "image-to-video"
              ]
            },
            "operationMatches": {
              "create": [
                "create",
                "generate"
              ]
            },
            "classMatches": {
              "workflow_or_script": [
                "cli"
              ]
            },
            "secondaryArtifacts": {
              "image": [
                "image"
              ],
              "audio": [
                "audio",
                "speech"
              ]
            },
            "secondaryOperations": {
              "automate": [
                "cli"
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
                "ai"
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
        "skillId": "global:00f913d69525ab2a",
        "skillName": "ai-avatar-video",
        "description": "Create AI avatar and talking head videos with OmniHuman, Fabric, PixVerse via inference.sh CLI. Models: OmniHuman 1.5, OmniHuman 1.0, Fabric 1.0, PixVerse Lipsync. Capabilities: audio-driven avatars, lipsync videos, talking head generation, virtual presenters. Use for: AI presenters, explainer videos, virtual influencers, dubbing, marketing videos. Triggers: ai avatar, talking head, lipsync, avatar video, virtual presenter, ai spokesperson, audio driven video, heygen alternative, synthesia alternative, talking avatar, lip sync, video avatar, ai presenter, digital human",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\ai-avatar-video",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\ai-avatar-video\\SKILL.md",
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
          "ai avatar",
          "talking head",
          "lipsync",
          "avatar video",
          "virtual presenter",
          "ai spokesperson",
          "audio driven video",
          "heygen alternative",
          "synthesia alternative",
          "talking avatar",
          "lip sync",
          "video avatar",
          "ai presenter",
          "digital human"
        ],
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
            "automate",
            "create"
          ],
          "interactionMode": "workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "audio",
            "image"
          ],
          "secondaryOperationHints": [
            "search"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "video": [
                "video",
                "videos"
              ]
            },
            "operationMatches": {
              "automate": [
                "cli"
              ],
              "create": [
                "create"
              ]
            },
            "classMatches": {
              "workflow_or_script": [
                "cli"
              ]
            },
            "secondaryArtifacts": {
              "audio": [
                "audio",
                "speech"
              ],
              "image": [
                "image",
                "images"
              ]
            },
            "secondaryOperations": {
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
                "ai"
              ]
            },
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
        "skillId": "global:f2d26475edec2f15",
        "skillName": "wechat-article-writer",
        "description": "公众号文章自动化写作流程。支持资料搜索、文章撰写、爆款标题生成、排版优化。当用户提到写公众号、微信文章、自媒体写作、爆款文章、内容创作时使用此 skill。",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\wechat-article-writer",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\wechat-article-writer\\SKILL.md",
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
        "directCanonicalFamilies": [
          "wechat-account-article",
          "wechat-account",
          "wechat"
        ],
        "canonicalFamilies": [
          "wechat-account-article",
          "wechat-account",
          "wechat"
        ],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "document"
          ],
          "primaryOperations": [
            "create"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.93,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code"
          ],
          "secondaryOperationHints": [
            "search",
            "automate",
            "advise",
            "guide"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "document": [
                "article",
                "文章",
                "文档"
              ]
            },
            "operationMatches": {
              "create": [
                "写",
                "生成"
              ]
            },
            "classMatches": {},
            "secondaryArtifacts": {
              "code": [
                "code",
                "代码"
              ]
            },
            "secondaryOperations": {
              "search": [
                "搜索"
              ],
              "automate": [
                "自动化"
              ],
              "advise": [
                "建议"
              ],
              "guide": [
                "教程"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "content_media",
            "writing_communication"
          ],
          "secondaryThemeTags": [
            "social_publishing"
          ],
          "themeConfidence": 0.81,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "content_media": [
                "wechat"
              ],
              "writing_communication": [
                "article"
              ]
            },
            "secondaryThemeMatches": {
              "social_publishing": [
                "wechat",
                "公众号",
                "公众号文章"
              ]
            }
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
      }
    ],
    "skillEntries": [
      {
        "skillId": "global:21909ae93fe53f6c",
        "skillName": "ai-video-generation",
        "description": "Generate AI videos with Google Veo, Seedance, Wan, Grok and 40+ models via inference.sh CLI. Models: Veo 3.1, Veo 3, Seedance 1.5 Pro, Wan 2.5, Grok Imagine Video, OmniHuman, Fabric, HunyuanVideo. Capabilities: text-to-video, image-to-video, lipsync, avatar animation, video upscaling, foley sound. Use for: social media videos, marketing content, explainer videos, product demos, AI avatars. Triggers: video generation, ai video, text to video, image to video, veo, animate image, video from image, ai animation, video generator, generate video, t2v, i2v, ai video maker, create video with ai, runway alternative, pika alternative, sora alternative, kling alternative",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\ai-video-generation",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\ai-video-generation\\SKILL.md",
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
          "video generation",
          "ai video",
          "text to video",
          "image to video",
          "veo",
          "animate image",
          "video from image",
          "ai animation",
          "video generator",
          "generate video",
          "t2v",
          "i2v",
          "ai video maker",
          "create video with ai",
          "runway alternative",
          "pika alternative",
          "sora alternative",
          "kling alternative"
        ],
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
            "create"
          ],
          "interactionMode": "workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "image",
            "audio"
          ],
          "secondaryOperationHints": [
            "automate"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "video": [
                "video",
                "video generation",
                "videos",
                "text-to-video",
                "image-to-video"
              ]
            },
            "operationMatches": {
              "create": [
                "create",
                "generate"
              ]
            },
            "classMatches": {
              "workflow_or_script": [
                "cli"
              ]
            },
            "secondaryArtifacts": {
              "image": [
                "image"
              ],
              "audio": [
                "audio",
                "speech"
              ]
            },
            "secondaryOperations": {
              "automate": [
                "cli"
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
                "ai"
              ]
            },
            "secondaryThemeMatches": {}
          }
        }
      },
      {
        "skillId": "global:00f913d69525ab2a",
        "skillName": "ai-avatar-video",
        "description": "Create AI avatar and talking head videos with OmniHuman, Fabric, PixVerse via inference.sh CLI. Models: OmniHuman 1.5, OmniHuman 1.0, Fabric 1.0, PixVerse Lipsync. Capabilities: audio-driven avatars, lipsync videos, talking head generation, virtual presenters. Use for: AI presenters, explainer videos, virtual influencers, dubbing, marketing videos. Triggers: ai avatar, talking head, lipsync, avatar video, virtual presenter, ai spokesperson, audio driven video, heygen alternative, synthesia alternative, talking avatar, lip sync, video avatar, ai presenter, digital human",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\ai-avatar-video",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\ai-avatar-video\\SKILL.md",
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
          "ai avatar",
          "talking head",
          "lipsync",
          "avatar video",
          "virtual presenter",
          "ai spokesperson",
          "audio driven video",
          "heygen alternative",
          "synthesia alternative",
          "talking avatar",
          "lip sync",
          "video avatar",
          "ai presenter",
          "digital human"
        ],
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
            "automate",
            "create"
          ],
          "interactionMode": "workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "audio",
            "image"
          ],
          "secondaryOperationHints": [
            "search"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "video": [
                "video",
                "videos"
              ]
            },
            "operationMatches": {
              "automate": [
                "cli"
              ],
              "create": [
                "create"
              ]
            },
            "classMatches": {
              "workflow_or_script": [
                "cli"
              ]
            },
            "secondaryArtifacts": {
              "audio": [
                "audio",
                "speech"
              ],
              "image": [
                "image",
                "images"
              ]
            },
            "secondaryOperations": {
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
                "ai"
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
        "skillId": "global:ea79d371a63649a1",
        "skillName": "skill-creator",
        "description": "Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Claude's capabilities with specialized knowledge, workflows, or tool integrations.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\skill-creator",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\skill-creator\\SKILL.md",
        "sourceType": "global",
        "visibility": "global",
        "workspacePath": "",
        "workspaceId": "",
        "projectId": "",
        "rootPath": "C:\\Users\\sunny\\.agents\\skills",
        "referencesDir": "C:\\Users\\sunny\\.agents\\skills\\skill-creator\\references",
        "scriptsDir": "C:\\Users\\sunny\\.agents\\skills\\skill-creator\\scripts",
        "assetsDir": "",
        "templatesDir": "",
        "examplesDir": "",
        "availableFiles": [
          "references/",
          "references/output-patterns.md",
          "references/workflows.md",
          "scripts/",
          "scripts/init_skill.py",
          "scripts/package_skill.py",
          "scripts/quick_validate.py"
        ],
        "aliases": [],
        "triggers": [],
        "keywords": [
          "with specialized knowledge",
          "workflows",
          "or tool integrations."
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
            "create",
            "guide"
          ],
          "interactionMode": "guided_workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code",
            "document"
          ],
          "secondaryOperationHints": [
            "convert",
            "edit"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "skill": [
                "skill-creator",
                "skill creator"
              ]
            },
            "operationMatches": {
              "create": [
                "create",
                "creating"
              ],
              "guide": [
                "guide",
                "guidance"
              ]
            },
            "classMatches": {
              "skill_authoring": [
                "skill-creator",
                "skill creator"
              ],
              "methodology_or_tutorial": [
                "guide"
              ],
              "workflow_or_script": [
                "scripts"
              ]
            },
            "secondaryArtifacts": {
              "code": [
                "scripts",
                "code"
              ],
              "document": [
                "markdown",
                "md"
              ]
            },
            "secondaryOperations": {
              "convert": [
                "transform"
              ],
              "edit": [
                "update"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "content_media"
          ],
          "secondaryThemeTags": [
            "specific_knowledge"
          ],
          "themeConfidence": 0.81,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "content_media": [
                "creator"
              ]
            },
            "secondaryThemeMatches": {
              "specific_knowledge": [
                "specific knowledge"
              ]
            }
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
      },
      {
        "skillId": "global:ffaa3a8ba976d59a",
        "skillName": "seedance-prompt-en",
        "description": "Write effective prompts for Jimeng Seedance 2.0 multimodal AI video generation. Use when users want to create video prompts using text, images, videos, and audio inputs with the @ reference system. Covers camera movements, effects replication, video extension, editing, music beat-matching, e-commerce ads, short dramas, and educational content.",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\seedance-prompt-en",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\seedance-prompt-en\\SKILL.md",
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
            "video",
            "audio"
          ],
          "primaryOperations": [
            "create"
          ],
          "interactionMode": "media_workflow",
          "capabilityConfidence": 0.98,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "image"
          ],
          "secondaryOperationHints": [
            "guide",
            "edit"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "video": [
                "video",
                "videos",
                "video generation"
              ],
              "audio": [
                "audio",
                "music"
              ]
            },
            "operationMatches": {
              "create": [
                "create",
                "generated"
              ]
            },
            "classMatches": {
              "methodology_or_tutorial": [
                "guide"
              ]
            },
            "secondaryArtifacts": {
              "image": [
                "images"
              ]
            },
            "secondaryOperations": {
              "guide": [
                "guide",
                "how to"
              ],
              "edit": [
                "editing"
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
        "skillId": "global:f2d26475edec2f15",
        "skillName": "wechat-article-writer",
        "description": "公众号文章自动化写作流程。支持资料搜索、文章撰写、爆款标题生成、排版优化。当用户提到写公众号、微信文章、自媒体写作、爆款文章、内容创作时使用此 skill。",
        "skillRoot": "C:\\Users\\sunny\\.agents\\skills\\wechat-article-writer",
        "instructionPath": "C:\\Users\\sunny\\.agents\\skills\\wechat-article-writer\\SKILL.md",
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
        "directCanonicalFamilies": [
          "wechat-account-article",
          "wechat-account",
          "wechat"
        ],
        "canonicalFamilies": [
          "wechat-account-article",
          "wechat-account",
          "wechat"
        ],
        "capabilityProfile": {
          "skillClass": "artifact_producer",
          "primaryArtifactTypes": [
            "document"
          ],
          "primaryOperations": [
            "create"
          ],
          "interactionMode": "file_workflow",
          "capabilityConfidence": 0.93,
          "profileSource": "rules",
          "secondaryArtifactHints": [
            "code"
          ],
          "secondaryOperationHints": [
            "search",
            "automate",
            "advise",
            "guide"
          ],
          "evidenceSignals": {
            "artifactMatches": {
              "document": [
                "article",
                "文章",
                "文档"
              ]
            },
            "operationMatches": {
              "create": [
                "写",
                "生成"
              ]
            },
            "classMatches": {},
            "secondaryArtifacts": {
              "code": [
                "code",
                "代码"
              ]
            },
            "secondaryOperations": {
              "search": [
                "搜索"
              ],
              "automate": [
                "自动化"
              ],
              "advise": [
                "建议"
              ],
              "guide": [
                "教程"
              ]
            }
          }
        },
        "themeProfile": {
          "primaryThemes": [
            "content_media",
            "writing_communication"
          ],
          "secondaryThemeTags": [
            "social_publishing"
          ],
          "themeConfidence": 0.81,
          "themeSource": "rules",
          "themeEvidenceSignals": {
            "primaryThemeMatches": {
              "content_media": [
                "wechat"
              ],
              "writing_communication": [
                "article"
              ]
            },
            "secondaryThemeMatches": {
              "social_publishing": [
                "wechat",
                "公众号",
                "公众号文章"
              ]
            }
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
        "rootPath": "C:\\Users\\sunny\\.v8-agent-os\\workspace\\.agents\\skills",
        "sourceType": "main_workspace",
        "workspacePath": "C:\\Users\\sunny\\.v8-agent-os\\workspace",
        "workspaceId": null,
        "projectId": null,
        "visibility": "global"
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
    "skillInventoryCount": 36,
    "skillPoolSize": 36,
    "skillStage1HitCount": 32,
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
    "crossRuntimeEscape": false,
    "pluginHostSeedCount": 0,
    "pluginHostBoundLimit": 12,
    "pluginHostBoundCount": 0,
    "totalInstalledSkills": 36,
    "totalConnectedMcpTools": 0,
    "totalPluginHostTools": 0,
    "agentCount": 0
  },
  "externalInputInstructionsPresent": false
}
```

## 模块解析摘要
| 模块 | 出现 | Estimated Tokens | 备注 |
|---|---:|---:|---|
| base prompt / system persona | Y | 466 |  |
| runtime registry / capability registry | N | 0 |  |
| specialist agent registry | N | 0 |  |
| direct tool registry | N | 0 |  |
| [NETWORK SUPERVISOR CONTEXT] | N | 0 | subagent 正常情况下不应继承 supervisor 级 network card。 |
| [EXTERNAL APP INSTRUCTIONS] | N | 0 |  |
| engineering context | Y | 251 |  |
| planner context / delegated task plan | Y | 245 |  |
| artifact awareness | N | 0 |  |
| todos / active task plan | N | 0 |  |
| memory profile | N | 0 |  |
| memory summary | N | 0 |  |
| memory map | N | 0 |  |
| workflow hints | N | 0 |  |
| workspace rules | N | 0 |  |
| environment | Y | 114 |  |
| extensions runtime route block | Y | 654 |  |
| interactive CLI rule | Y | 141 |  |
| group moderation / execution hints | N | 0 |  |

## SYSTEM_CONTENT 全文
```text
<system_persona>
You are a specialized agent named Research Synthesizer.
You are Research Synthesizer, a V8 Agent OS specialist subagent.

Shared V8 subagent discipline:
- Start from the delegated task brief, not the whole supervisor conversation. Restate only the assumptions that affect your slice.
- Keep the solution surgical: no speculative abstractions, no adjacent cleanup, no unrequested scope expansion.
- Preserve runtime boundaries. Subagents do not have ComputerUse, RPA, or Memory runtime authority by default; ask the supervisor to route those actions.
- Define evidence before claiming completion. Report exact checks run, artifacts produced, blockers, and residual risk.
- Return compact, aggregatable output for the supervisor. Local self-check is not final acceptance.

Mission:
- Produce compact, source-aware research that helps the supervisor decide or brief another worker.
- Separate confirmed facts, plausible inferences, and unknowns.

Input contract:
- A research question, target audience, freshness requirement, and output format or decision to support.

Operating protocol:
- Start by defining what evidence would change the answer.
- Prefer primary or authoritative sources; note when only secondary sources are available.
- Compare alternatives on criteria relevant to the delegated task.
- Stop when the marginal source no longer changes the decision.

Output contract:
- Short answer, evidence matrix, key tradeoffs, confidence, and recommended next action.
- Include links or source identifiers when available through the route-selected tools.

Verification contract:
- Check source recency and relevance. Mark any claim that relies on inference rather than direct evidence.

Boundaries and refusal rules:
- Do not perform implementation.
- Do not over-collect sources when a narrow decision is needed.
- Do not blur source-backed facts with speculation.

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

<delegated_task_plan>
You are executing one bounded task from the supervisor's planner/delegation pipeline.
Use this local task contract as the routing truth; do not reinterpret the original user request as your primary scope.

Assigned Task Brief:
- Task Brief ID: network-api-structured-summary
- Goal: 为第三方应用调用生成结构化结果说明与风险备注，保证输出可被外部应用直接消费。
- Context: 当前运行在 OpenAI-compatible API transport，上游是外部应用而不是 V8 phone/web。
- Behavior Scope: summarize, format, compatibility
- Required Capabilities: writing, analysis, structured_output
- Execution Lane Hint: network_supervisor_openai
- Acceptance Contract: 输出精简结构化说明，不引用 V8 内部卡片、不假设 ask_user 或 artifact card 可见。

Engineering Task Capsule:
- Engineering Role: review
- Write Discipline: Treat this task as read-only. Do not modify production files unless the supervisor explicitly grants a writeSet.
</delegated_task_plan>

[Extensions Runtime]
- Skills 候选：10 / 已安装 36
- MCP 工具候选：0 / 已连接工具 0
- 候选预筛：当前使用第 1 层 shortlist。
- 当前命中的 Skills 目录入口：
  - ai-video-generation [global]
    - Skill description: Generate AI videos with Google Veo, Seedance, Wan, Grok and 40+ models via inference.sh CLI. Models: Veo 3.1, Veo 3, Seedance 1.5 Pro, Wan 2.5, Grok Imagine Video, OmniHuman, Fa...
  - ai-avatar-video [global]
    - Skill description: Create AI avatar and talking head videos with OmniHuman, Fabric, PixVerse via inference.sh CLI. Models: OmniHuman 1.5, OmniHuman 1.0, Fabric 1.0, PixVerse Lipsync. Capabilities:...
  - huashu-nuwa [global]
    - Skill description: 女娲造人：输入人名/主题/甚至只是模糊需求，自动深度调研→思维框架提炼→生成可运行的人物Skill。 两种入口：(1)明确人名→直接蒸馏 (2)模糊需求→诊断推荐→再蒸馏。 触发词：「造skill」「蒸馏XX」「女娲」「造人」「XX的思维方式」「做个XX视角」「更新XX的skill」。 模糊需求也触发：「我想提升决策质量」「有没有一种思维方式能帮我.....
  - docx [global]
    - Skill description: Comprehensive document creation, editing, and analysis with support for tracked changes, comments, formatting preservation, and text extraction. When Claude needs to work with p...
  - pptx [global]
    - Skill description: Presentation creation, editing, and analysis. When Claude needs to work with presentations (.pptx files) for: (1) Creating new presentations, (2) Modifying or editing content, (...
  - skill-creator [global]
    - Skill description: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Claude's capabilities with specia...
  - xlsx [global]
    - Skill description: Comprehensive spreadsheet creation, editing, and analysis with support for formulas, formatting, data analysis, and visualization. When Claude needs to work with spreadsheets (....
  - seedance-prompt-en [global]
    - Skill description: Write effective prompts for Jimeng Seedance 2.0 multimodal AI video generation. Use when users want to create video prompts using text, images, videos, and audio inputs with the...
  - frontend-design [global]
    - Skill description: Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, artifacts, posters, or appli...
  - wechat-article-writer [global]
    - Skill description: 公众号文章自动化写作流程。支持资料搜索、文章撰写、爆款标题生成、排版优化。当用户提到写公众号、微信文章、自媒体写作、爆款文章、内容创作时使用此 skill。
[/Extensions Runtime]

[Interactive CLI Rule]
Use `run_system_command` only for short synchronous commands.
Use `command_session_broker(mode=start)` for long-running commands, interactive CLIs/REPLs, and dev servers; continue with `observe`, `input`, or `terminate`.
Treat broker JSON (`summary`, `recommendedNextAction`, `awaitingInput`, `hasMore`, `status`) as the primary truth; use `debug=true` only for raw terminal diagnostics.
If terminal automation or observation is uncertain, report that uncertainty instead of inventing progress.

When you have fully completed your assigned task, respond with your findings or status to return control to the supervisor.
```
