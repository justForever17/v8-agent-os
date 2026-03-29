
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
    console.log("🔍 Diagnosing Agent Configuration...");

    // 1. Find the specific agent
    const agentName = "创意页面设计师";
    const agent = await prisma.agent.findFirst({
        where: { name: agentName },
        include: {
            model: {
                include: {
                    provider: true
                }
            }
        }
    });

    if (!agent) {
        console.error(`❌ Agent '${agentName}' not found!`);

        console.log("\nListing all available agents:");
        const separateAgents = await prisma.agent.findMany({ select: { name: true } });
        separateAgents.forEach(a => console.log(` - ${a.name}`));
        return;
    }

    console.log(`\n✅ Found Agent: ${agent.name} (ID: ${agent.id})`);
    console.log("---------------------------------------------------");
    console.log(`Linked AI Model:`);
    console.log(` - Name: ${agent.model.name}`);
    console.log(` - Model ID (API): ${agent.model.modelId}`);
    console.log(` - DB ID: ${agent.model.id}`);

    console.log(`\nLinked AI Provider:`);
    console.log(` - Name: ${agent.model.provider.name}`);
    console.log(` - Code: ${agent.model.provider.code}`);
    console.log(` - Base URL: '${agent.model.provider.baseUrl}' [${agent.model.provider.baseUrl ? 'PRESENT' : 'MISSING/EMPTY'}]`);
    console.log(` - API Key: ${agent.model.provider.apiKey ? '****** (Present)' : 'MISSING'}`);
    console.log(` - DB ID: ${agent.model.provider.id}`);

    // 2. Check if a "DeepSeek" provider exists separately (to check for duplicates/confusion)
    const deepseekProvider = await prisma.aIProvider.findUnique({
        where: { code: 'deepseek' }
    });

    if (deepseekProvider) {
        console.log("\n---------------------------------------------------");
        console.log("🔎 Checking 'deepseek' Provider (Standard Entry):");
        console.log(` - ID: ${deepseekProvider.id}`);
        console.log(` - Base URL: '${deepseekProvider.baseUrl}'`);

        if (deepseekProvider.id !== agent.model.provider.id) {
            console.warn("\n⚠️  WARNING: The Agent is NOT linked to this standard 'deepseek' provider!");
            console.warn(`   Agent is linked to Provider ID: ${agent.model.provider.id}`);
            console.warn(`   'deepseek' Provider ID is:      ${deepseekProvider.id}`);
            console.warn("   -> This means the Agent is using a DIFFERENT provider entry (maybe 'openai' with a deepseek model name?)");
        } else {
            console.log("\n✅ The Agent is correctly linked to the standard 'deepseek' provider.");
        }
    } else {
        console.log("\nℹ️  No provider with code='deepseek' found in DB.");
    }

}

main()
    .catch((e) => {
        console.error(e);
        process.exit(1);
    })
    .finally(async () => {
        await prisma.$disconnect();
    });
