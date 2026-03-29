import os
import logging
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("NightlyMemoryBatch")

def run(payload: dict = None):
    """
    Nightly batch job to summarize daily logs into weekly summaries.
    This runs via ActionExecutor when scheduled by CronManager.
    """
    logger.info("Starting Nightly Memory Batch Job...")
    
    try:
        from core.memory_router import MemoryRouter
        from core.knowledge_db import knowledge_db
        from langchain_core.messages import SystemMessage, HumanMessage
        from langchain_core.output_parsers import PydanticOutputParser
        from pydantic import BaseModel, Field
        from typing import List
        from runtimes.memory.runtime import memory_runtime
        from runtimes.memory.prompts import (
            render_memory_consolidation_prompt,
            render_periodic_summary_prompt,
        )
        
        # Pydantic Schemas for Consolidation
        class MergeEntityAction(BaseModel):
            source_entity: str = Field(description="The duplicate entity name to be removed (e.g., 'reactjs')")
            target_entity: str = Field(description="The canonical entity name to keep (e.g., 'react')")

        class DeleteFactAction(BaseModel):
            fact_id: str = Field(description="ID of the fact to be deleted because it is outdated or conflicting")

        class ConsolidationResult(BaseModel):
            merge_entities: List[MergeEntityAction] = Field(default_factory=list, description="List of entity merges to perform")
            delete_facts: List[DeleteFactAction] = Field(default_factory=list, description="List of outdated facts to delete")

        def _get_consolidation_prompt(format_instructions: str) -> str:
            return render_memory_consolidation_prompt(format_instructions)
        
        # Determine the target date (default to yesterday for nightly jobs)
        target_date = datetime.now() - timedelta(days=1)
        if payload and "target_date" in payload:
            try:
                target_date = datetime.strptime(payload["target_date"], "%Y-%m-%d")
            except ValueError:
                logger.warning(f"Invalid target_date format: {payload['target_date']}. Using yesterday.")
        
        target_date_str = target_date.strftime("%Y-%m-%d")
        logger.info(f"Processing summaries up to: {target_date_str}")
        
        # 1. Fetch recent daily logs (e.g., last 7 days)
        recent_logs = []
        for i in range(7):
            dt = target_date - timedelta(days=i)
            log_content = memory_runtime.read_memory_summary(tier="day", date_str=dt.strftime("%Y-%m-%d"))
            if not log_content.startswith("No daily log found"):
                recent_logs.append(log_content)
                
        if not recent_logs:
            logger.info("No recent daily logs found to summarize. Skipping.")
            return

        compiled_logs = "\n\n---\n\n".join(reversed(recent_logs))
        
        # 2. Get LLM for extraction
        router = MemoryRouter()
        try:
            llm = router.get_extractor_llm()
        except ValueError as ve:
            logger.error(f"LLM Configuration missing for MemoryRouter: {ve}")
            return
        
        # 3. Generate Weekly Summary
        system_prompt = render_periodic_summary_prompt(tier="week", content=compiled_logs)
        
        logger.info("Calling LLM to generate summary...")
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content="Generate the weekly memory summary from the provided scoped recent logs.")
        ])
        
        summary_content = response.content
        
        # 4. Save the generated summary
        memory_runtime.save_periodic_summary(tier="week", content=summary_content, dt=target_date)
        logger.info("Weekly Summary generated and saved.")
        
        # 5. Graph Consolidation & Disambiguation
        logger.info("Starting Graph Consolidation Step...")
        try:
            with knowledge_db._conn() as conn:
                entities_rows = conn.execute("SELECT name, type FROM entities LIMIT 500").fetchall()
                entities_str = "\n".join([f"- {r['name']} ({r['type']})" for r in entities_rows])
                
                facts_rows = conn.execute(
                    "SELECT id, fact, category, scope FROM knowledge WHERE status='active' ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
                facts_str = "\n".join([
                    f"- [id: {r['id']}] [scope: {r['scope']}] [{r['category']}] {r['fact']}"
                    for r in facts_rows
                ])
                
            parser = PydanticOutputParser(pydantic_object=ConsolidationResult)
            consolidation_system_prompt = _get_consolidation_prompt(parser.get_format_instructions())
            
            consolidation_response = llm.invoke([
                SystemMessage(content=consolidation_system_prompt),
                HumanMessage(content=f"Current Entities (Sample):\n{entities_str}\n\nRecent Facts:\n{facts_str}")
            ])
            
            str_content = consolidation_response.content
            if "<think>" in str_content and "</think>" in str_content:
                str_content = str_content.split("</think>")[-1].strip()
                
            consolidation_result = parser.invoke(str_content)
            
            # Execute Merges
            merges_count = len(consolidation_result.merge_entities)
            deletes_count = len(consolidation_result.delete_facts)
            
            if merges_count > 0 or deletes_count > 0:
                with knowledge_db._conn() as conn:
                    for merge in consolidation_result.merge_entities:
                        src, tgt = merge.source_entity, merge.target_entity
                        # Delete relations that would become duplicates after merge
                        conn.execute("""
                            DELETE FROM relations WHERE subject = ? AND (predicate, object) IN 
                            (SELECT predicate, object FROM relations WHERE subject = ?)
                        """, (src, tgt))
                        conn.execute("""
                            DELETE FROM relations WHERE object = ? AND (subject, predicate) IN 
                            (SELECT subject, predicate FROM relations WHERE object = ?)
                        """, (src, tgt))
                        # Now safely update remaining relations
                        conn.execute("UPDATE relations SET subject = ? WHERE subject = ?", (tgt, src))
                        conn.execute("UPDATE relations SET object = ? WHERE object = ?", (tgt, src))
                        # Ensure target entity exists, then delete source
                        conn.execute("INSERT OR IGNORE INTO entities (name, type) SELECT ?, type FROM entities WHERE name = ?", (tgt, src))
                        conn.execute("DELETE FROM entities WHERE name = ?", (src,))
                        logger.info(f"[Consolidation] Merged entity '{src}' -> '{tgt}'")
                        
                for d_fact in consolidation_result.delete_facts:
                    memory_runtime.delete_knowledge(fact_id=d_fact.fact_id)
                    logger.info(f"[Consolidation] Deleted fact '{d_fact.fact_id}'")
                    
                # Trigger incremental index if things changed
                knowledge_db.run_incremental_index()
                
            # Phase 25.3: Orphaned Node and Relation Cleanup
            with knowledge_db._conn() as conn:
                # 1. Clean relations tied to deleted facts
                res_rels = conn.execute("DELETE FROM relations WHERE source_fact_id IS NOT NULL AND source_fact_id NOT IN (SELECT id FROM knowledge)")
                deleted_rels = res_rels.rowcount
                
                # 2. Clean isolated entities
                res_ents = conn.execute("DELETE FROM entities WHERE name NOT IN (SELECT subject FROM relations) AND name NOT IN (SELECT object FROM relations)")
                deleted_ents = res_ents.rowcount
                
                if deleted_rels > 0 or deleted_ents > 0:
                    logger.info(f"[Consolidation Cleanup] Removed {deleted_rels} orphaned relations and {deleted_ents} orphaned entities.")
                
            logger.info(f"Graph Consolidation completed: {merges_count} merges, {deletes_count} deletions, {deleted_rels} relation cleanups, {deleted_ents} entity cleanups.")
            
        except Exception as e:
            logger.error(f"Graph Consolidation step failed: {e}")
            
        logger.info("Nightly Memory Batch Job completed successfully.")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Failed to run Nightly Memory Batch Job: {e}")

if __name__ == "__main__":
    run()
