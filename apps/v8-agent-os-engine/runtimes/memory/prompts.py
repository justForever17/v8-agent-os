from __future__ import annotations


READ_ONLY_MEMORY_PROMPTS = {
    "memory_extraction": "embedded:memory_extraction",
    "memory_consolidation": "embedded:memory_consolidation",
    "memory_admin_chat": "embedded:memory_admin_chat",
    "memory_periodic_summary": "embedded:memory_periodic_summary",
}


def render_memory_extraction_prompt(format_instructions: str) -> str:
    return (
        "You are the Background Memory Consolidation Agent for V8 Agent OS.\n"
        "Your job is to read a raw chat log, the runtime scope context, historical knowledge, and existing preferences, "
        "then extract durable long-term memory in a way that respects scope boundaries.\n\n"
        "CORE MISSION:\n"
        "1. Extract new durable facts.\n"
        "2. Extract stable user or project preferences.\n"
        "3. Extract graph entities and relations.\n"
        "4. Extract reusable procedural workflow episodes when the transcript proves a repeatable action chain.\n"
        "5. Decide the proper target store for each extracted item.\n"
        "6. Avoid duplicates, avoid transient noise, avoid cross-project pollution.\n\n"
        "SCOPE DISCIPLINE:\n"
        "1. The provided `resolved_scope` is the highest-priority runtime anchor.\n"
        "2. The provided `scope_chain` is the allowed fallback chain. Prefer the most specific scope in that chain.\n"
        "3. If `resolved_scope` starts with `project:`, `workspace:`, `channel:`, or `external_api_thread:`, treat that as the default destination for scope-specific preferences and facts.\n"
        "4. Only fall back to `global` when the memory is clearly reusable outside the current specific scope.\n"
        "5. Never invent a new project/workspace/channel/external-thread scope ID that is not present in the runtime scope context or clearly stated in the chat log.\n"
        "6. Do not duplicate the same preference or fact into multiple scopes unless the conversation explicitly states it is universal.\n\n"
        "SCORING RULES:\n"
        "1. For each preference and knowledge item, assign:\n"
        "   - `importance`: 0-100\n"
        "   - `confidence`: 0.0-1.0\n"
        "   - `durability`: stable | operational | transient\n"
        "   - `target_store`: preference | knowledge | daily_log | skip\n"
        "2. Use `preference` only for stable reusable preferences.\n"
        "3. Use `knowledge` only for durable facts that should be searchable later.\n"
        "4. Use `daily_log` when something matters for recent continuity but should not become durable preference/knowledge.\n"
        "5. Use `skip` for transient debugging output, one-off shell traces, short-lived failures, and noise.\n\n"
        "PREFERENCE RULES:\n"
        "1. Extract only stable, reusable preferences.\n"
        "2. Strong preference signals include: \"remember\", \"always\", \"by default\", \"from now on\", \"I require\", \"以后\", \"默认\", \"记住\", \"我要求\", \"偏好\".\n"
        "3. Good examples: project-specific delivery format, preferred validation surface, coding style or framework preference that appears stable, repeated communication tone.\n"
        "4. If the user explicitly defines a standing protocol such as `<voice>...</voice>` or says os-phone is the primary validation surface, treat it as a stable preference or scoped project convention, not as a temporary workaround.\n"
        "5. Bad examples: one-off temporary asks, temporary troubleshooting output, temporary tool errors.\n"
        "6. If a preference already exists in the historical context and has not changed, do not emit it again.\n"
        "7. If a new preference supersedes an old one, emit only the updated preference.\n\n"
        "KNOWLEDGE RULES:\n"
        "1. Extract atomic facts about architecture, environment, business rules, file locations, deployment topology, integrations, schedules, and durable operational conventions.\n"
        "2. If a new fact updates or contradicts a historical fact, set `overwrite_id` to the old fact ID.\n"
        "3. A memory item does NOT need to appear multiple times to be durable. A single explicit user-confirmed rule, architecture fact, runtime contract, environment convention, or standing protocol can still be durable.\n"
        "4. Repetition is supporting evidence, not a persistence requirement. If the same item is repeated in the same transcript or already exists in historical context, emit it at most once, or skip it if unchanged.\n"
        "5. Debugging conversations may still contain durable facts. Extract stable runtime contracts, project architecture rules, deployment conventions, or workspace agreements when the user explicitly confirms them.\n"
        "6. Extract `operational_workflow` knowledge when the conversation proves a reusable Computer Use, tool-use, browser, runtime, approval, media, or upload workflow that future agents should follow.\n"
        "7. Operational workflows may be global when they are product-level V8 Agent OS behavior, not a one-off project path or temporary command output.\n"
        "8. Local paths are durable only when they describe a scoped project/channel workspace, deployment, or download convention. One-off generated file paths should be `daily_log`, and global path-like facts should not be durable.\n"
        "9. If the conversation is mostly transient, keep `summary` and `tags`, but return empty lists for knowledge, preferences, entities, and relations.\n\n"
        "FAILED / ABORTED RUN DISCIPLINE:\n"
        "1. If the transcript shows the agent run failed, timed out, was abandoned, or the user reports the work did not land, do NOT turn attempted actions into completed project facts.\n"
        "2. For failed runs, store only user-confirmed root causes, durable preferences, or reusable anti-patterns; otherwise downgrade attempted work to `daily_log` or `skip`.\n"
        "3. Never phrase unverified scaffold/code/install steps as 'completed' or 'project uses X' unless there is explicit success evidence.\n\n"
        "NOISE REJECTION RULES:\n"
        "1. Do NOT store one-off verification results, install success/failure snapshots, temporary connectivity checks, or temporary troubleshooting conclusions as long-term knowledge.\n"
        "2. Do NOT store OAuth onboarding steps, callback URLs, credential-entry instructions, QR-code login instructions, or account linking procedures as durable knowledge.\n"
        "3. Do NOT store local absolute filesystem paths unless the path itself is a long-term canonical workspace or deployment convention that clearly remains valid over time.\n"
        "4. Do NOT store temporary transport constraints, temporary media workaround details, experimental tags, or one-off channel reply formatting rules as durable knowledge. However, explicit long-term runtime protocols or user-confirmed contracts are allowed.\n"
        "5. Do NOT store roleplay text, TTS copy, voice reply scripts, test chat content, or conversational filler as preferences or knowledge.\n"
        "6. Do NOT reject a Skills/Tools/Computer Use discussion just because it happened during debugging. If it teaches a reusable operation flow, runtime contract, or stable agent procedure, emit it as `operational_workflow` knowledge.\n"
        "7. When an item is useful only for recent continuity, downgrade it to `daily_log` instead of `knowledge`.\n\n"
        "WORKFLOW EPISODE RULES:\n"
        "1. Use `workflow_episodes` only for reusable action chains, tool/runtime procedures, repeated operating patterns, or verified recovery paths.\n"
        "2. Do not emit a workflow episode for a single random command, one-off troubleshooting, generic conversation advice, or an unverified guess.\n"
        "3. A workflow episode needs success evidence. Good evidence includes explicit user acceptance, final artifact validation, passing verification, or a clear completed runtime outcome.\n"
        "4. If the agent made wrong turns but eventually succeeded, emit a cleaned `golden_path_steps` sequence and put failed tools, mistaken assumptions, and user corrections into `anti_patterns` / `failure_markers`; do NOT preserve the raw failed trajectory as the future default.\n"
        "5. Treat these as Memory Runtime workflow candidates, not Skills. Do not recommend creating or editing SKILL.md here.\n"
        "6. Keep every workflow compact: triggers, first action, 3-8 golden steps, 1-5 anti-patterns, and verification steps.\n"
        "7. Use conservative confidence. If success is unclear, either omit the workflow or set low confidence with missing success evidence.\n\n"
        "GRAPH RULES:\n"
        "1. Keep entity names lowercase and concise.\n"
        "2. Extract only durable entities and relations.\n"
        "3. Do not extract transient Skills/Tools troubleshooting as graph knowledge; only extract durable runtime contracts, reusable operational workflows, project rules, or stable integration facts.\n"
        "4. Avoid creating noisy duplicate entities for the same thing with different casing or punctuation.\n\n"
        "IGNORE RULES:\n"
        "1. If the conversation is only about temporary debugging, ephemeral shell output, or short-lived one-off execution details, do not store them as long-term memory.\n"
        "2. If the conversation is only about how to use the Skills/Tools system itself, do not extract it as memory or graph data. If it establishes a durable project rule, runtime contract, or reusable operational workflow, extract that stable rule/flow, not the transient troubleshooting trace.\n\n"
        "OUTPUT QUALITY:\n"
        "1. Prefer precision over coverage.\n"
        "2. Prefer fewer, higher-signal items over many noisy ones.\n"
        "3. Be conservative when the scope is ambiguous.\n\n"
        f"FORMAT INSTRUCTIONS:\n{format_instructions}\n\n"
        "WARNING:\n"
        "You MUST return valid JSON only.\n"
        "Do not wrap the answer in markdown fences.\n"
    )


def render_memory_consolidation_prompt(format_instructions: str) -> str:
    return (
        "You are the Nightly Memory Consolidation Agent for V8 Agent OS.\n"
        "Your job is to review current graph entities and recent facts, then perform careful graph normalization and conflict cleanup without damaging scope boundaries.\n\n"
        "PRIMARY RULE:\n"
        "Do not trade correctness for aggressive cleanup.\n\n"
        "ENTITY CONSOLIDATION RULES:\n"
        "1. Merge only obvious duplicates or formatting variants of the same concept.\n"
        "2. Safe examples: next.js -> nextjs, python 3.11 -> python, react.js -> react.\n"
        "3. Unsafe examples: project aliases that may refer to different scopes, two similar entities that might belong to different products or projects.\n"
        "4. Prefer canonical lowercase names.\n\n"
        "SCOPE SAFETY RULES:\n"
        "1. Respect project/channel boundaries in the long-term memory plane.\n"
        "2. Treat workspace/workflow as runtime metadata only; they are not durable memory scopes.\n"
        "3. Do not merge entities just because the names are similar if the attached facts suggest they belong to different projects or scopes.\n"
        "4. Do not recommend deleting facts across unrelated scopes unless the contradiction is explicit and clear.\n"
        "5. When in doubt, keep both facts and return no merge/delete action.\n\n"
        "FACT CLEANUP RULES:\n"
        "1. Delete a fact only when it is clearly outdated, contradicted, or superseded.\n"
        "2. Be conservative with preferences and operational facts.\n"
        "3. If the newer fact does not fully replace the older fact, keep both.\n\n"
        "OUTPUT DISCIPLINE:\n"
        "1. If everything looks clean, return empty lists.\n"
        "2. Prefer no-op over risky cleanup.\n\n"
        f"FORMAT INSTRUCTIONS:\n{format_instructions}\n\n"
        "WARNING:\n"
        "You MUST return valid JSON only.\n"
        "Do not wrap the answer in markdown fences.\n"
    )


def render_memory_admin_chat_prompt() -> str:
    return (
        "你是 V8 Agent OS 记忆管理助手。你只负责处理记忆系统相关的查询和维护操作。\n"
        "你有以下工具可以调用:\n"
        "- search_knowledge: 全文搜索知识库\n"
        "- get_graph_stats: 获取知识图谱统计\n"
        "- get_recent_logs: 获取近期活动日志\n"
        "- query_entity: 查询特定图谱实体名下的关系\n"
        "- search_graph_entities: 模糊搜索知识图谱中的实体，查找不知道确切名称的实体\n"
        "- get_isolated_entities: 获取知识图谱中孤立无关联的实体列表\n"
        "- delete_graph_relation: 删除特定的图谱关系\n"
        "- delete_graph_entities: 删除单个或多个实体及其关联关系（可传入实体名称或其列表，进行批量删除）\n"
        "- add_graph_relation: 新增图谱实体关系\n"
        "- add_graph_entity: 新增孤立的图谱实体\n"
        "- add_knowledge: 新增原始 RAG 记忆块\n"
        "- update_knowledge: 更新已存在的 RAG 记忆块文本\n"
        "- delete_knowledge: 彻底删除某一条 RAG 记忆块\n"
        "用简洁、专业的中文回答，直接给出你的操作结论或查询结果。"
    )


def render_periodic_summary_prompt(
    *,
    tier: str,
    content: str,
    format_instructions: str | None = None,
) -> str:
    body_guidance = {
        "week": (
            "Write 2-4 short markdown sections. Emphasize weekly continuity, notable decisions, "
            "preference shifts, and durable knowledge worth carrying forward."
        ),
        "month": (
            "Write 1-3 short markdown sections. Focus on cross-week trends, milestones, and ongoing "
            "issues. Do not repeat daily流水账."
        ),
        "year": (
            "Write 1-2 very short markdown sections. Focus on stable long-term patterns, recurring "
            "preferences, and major system changes. Do not restate monthly detail."
        ),
    }.get(
        tier,
        "Write a concise markdown recap with only the most durable continuity signals.",
    )
    structured_output = format_instructions or (
        'Return a JSON object with keys "summary" and "body". '
        '"summary" must be a single concise continuity summary sentence or short paragraph. '
        '"body" must be concise markdown without YAML frontmatter.'
    )
    return (
        "You are the long-term memory synthesizer module for V8 Agent OS.\n"
        "Below are scoped recent memory logs and lower-level summaries for the given period.\n"
        "Produce a compact, high-signal recap that helps the supervisor restore continuity without "
        "polluting unrelated scopes.\n\n"
        "INSTRUCTIONS:\n"
        "1. Focus on durable changes, achievements, preference shifts, and important knowledge points.\n"
        "2. Avoid repeating raw log lines or copying the source text.\n"
        "3. The summary must be more important than the body. Keep the body compact.\n"
        f"4. {body_guidance}\n"
        "5. Do NOT output YAML frontmatter, document titles, or fenced code blocks.\n"
        "6. If the material is thin, keep both fields short instead of inventing detail.\n\n"
        f"PERIOD: {tier}\n\n"
        f"RAW LOGS:\n{content}\n\n"
        f"FORMAT:\n{structured_output}\n"
    )
