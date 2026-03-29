
import { ChatOpenAI } from "@langchain/openai";
import { AIMessage } from "@langchain/core/messages";
import { ReasoningExtractor, createJsonFallbackChain } from "../src/lib/langchain/parsers";
import { z } from "zod";

// Mock environment
process.env.OPENAI_API_KEY = "sk-mock";

async function runVerification() {
    console.log("=== Supervisor Architecture Verification ===");

    // 1. Verify ReasoningExtractor Normalization
    console.log("\n[Test 1] ReasoningExtractor Normalization");
    const extractor = new ReasoningExtractor();

    // Case A: content has NO think tag, but additional_kwargs has reasoning_content (DeepSeek style)
    const deepSeekMsg = new AIMessage({
        content: "Here is the answer.",
        additional_kwargs: { reasoning_content: "Reflecting on the user request..." },
        id: "1"
    });

    const normalizedA = await extractor.invoke(deepSeekMsg);
    console.log("Input A (DeepSeek Field):", deepSeekMsg);
    console.log("Output A:", normalizedA.content);

    if (typeof normalizedA.content === 'string' && normalizedA.content.includes("<think>Reflecting")) {
        console.log("✅ PASS: Injected <think> tag.");
    } else {
        console.error("❌ FAIL: Did not inject <think> tag.");
    }

    // Case B: content ALREADY has think tag (Legacy/Native)
    const nativeMsg = new AIMessage({
        content: "<think>Thinking...</think>Answer.",
        additional_kwargs: {},
        id: "2"
    });

    const normalizedB = await extractor.invoke(nativeMsg);
    console.log("Input B (Native Tag):", nativeMsg.content);
    console.log("Output B:", normalizedB.content);

    if ((normalizedB.content as string).match(/<think>/g)?.length === 1) {
        console.log("✅ PASS: Preserved existing tag (no double wrap).");
    } else {
        console.error("❌ FAIL: Double wrapped or lost tag.");
    }

    // 2. Verify Fallback Chain Construction
    console.log("\n[Test 2] Fallback Chain Construction");
    const model = new ChatOpenAI({ modelName: "gpt-3.5-turbo" });
    const schema = z.object({ task: z.string() });

    try {
        await createJsonFallbackChain(model, schema, "SysProto", "TaskPrompt");
        console.log("✅ PASS: Fallback chain created successfully.");
    } catch (e) {
        console.error("❌ FAIL: Fallback chain creation failed:", e);
    }
}

runVerification().catch(console.error);
