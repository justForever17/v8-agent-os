
import { PrismaClient } from "@prisma/client";
import { ChatOpenAI } from "@langchain/openai";
import dotenv from "dotenv";

dotenv.config();

const prisma = new PrismaClient();

async function verifyDeepSeekStandardization() {
    try {
        console.log("🔍 Connecting to DB to fetch DeepSeek configuration...");

        // Find DeepSeek provider
        const provider = await prisma.aIProvider.findFirst({
            where: {
                name: {
                    contains: "DeepSeek",
                    mode: "insensitive"
                }
            }
        });

        if (!provider || !provider.apiKey) {
            console.error("❌ DeepSeek provider NOT found or has no API Key in DB.");
            process.exit(1);
        }

        console.log(`✅ Found Provider: ${provider.name}`);
        const apiKey = provider.apiKey;
        const baseUrl = provider.baseUrl || "https://api.deepseek.com/v1";

        console.log("🔍 Verifying DeepSeek V3 with standard ChatOpenAI...");

        const model = new ChatOpenAI({
            modelName: "deepseek-chat",
            openAIApiKey: apiKey,
            configuration: {
                baseURL: baseUrl,
            },
            streaming: true,
            temperature: 0.6
        });

        console.log("--- TEST 1: Streaming Reasoning Content ---");
        console.log("Prompt: '9.11 and 9.8, which is bigger?' (Expect reasoning)");

        const stream = await model.stream("9.11 and 9.8, which is bigger?");

        let fullContent = "";
        let foundReasoning = false;
        let reasoningType = "none";

        for await (const chunk of stream) {
            const additionalKwargs = chunk.additional_kwargs;
            const content = chunk.content;

            // 1. Check for standard 'reasoning_content'
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            if ((additionalKwargs as any)?.reasoning_content) {
                if (!foundReasoning) {
                    console.log("✅ Found 'reasoning_content' in additional_kwargs!");
                    foundReasoning = true;
                    reasoningType = "additional_kwargs.reasoning_content";
                }
                process.stdout.write("R");
                continue;
            }

            // 2. Check for <think> tags in content
            if (typeof content === 'string' && content.includes('<think>')) {
                if (!foundReasoning) {
                    console.log("\n⚠️ Found '<think>' tags in content (Raw XML)");
                    foundReasoning = true;
                    reasoningType = "raw_xml";
                }
            }

            if (typeof content === 'string') {
                fullContent += content;
            }
        }

        console.log("\n\n--- RESULT ---");
        console.log(`Reasoning Detected: ${foundReasoning}`);
        console.log(`Reasoning Type: ${reasoningType}`);
        console.log("Full Content Preview:", fullContent.substring(0, 100) + "...");

    } catch (e) {
        console.error("❌ Test Failed:", e);
    } finally {
        await prisma.$disconnect();
    }
}

verifyDeepSeekStandardization();
