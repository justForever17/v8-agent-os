
import { prisma } from "../src/lib/db";

async function main() {
    const toolNames = [
        "maps_regeocode", "maps_geo", "maps_ip_location", "maps_weather", "maps_search_detail",
        "maps_bicycling", "maps_direction_walking", "maps_direction_driving", "maps_direction_transit_integrated",
        "maps_distance", "maps_text_search", "maps_around_search",
        "resolve-library-id", "get-library-docs",
        "brave_web_search", "brave_local_search"
    ];

    console.log("Updating 'Chat' agent with tools:", toolNames);

    // Find the 'Chat' agent (formerly skill)
    const chatAgent = await prisma.agent.findFirst({
        where: {
            OR: [
                { name: '聊天' },
                { name: 'Chat' }
            ]
        }
    });

    if (!chatAgent) {
        console.error("Agent '聊天' or 'Chat' not found!");
        return;
    }

    console.log(`Found agent: ${chatAgent.name} (${chatAgent.id})`);

    // Update the agent
    await prisma.agent.update({
        where: { id: chatAgent.id },
        data: {
            tools: toolNames
        }
    });

    console.log("Agent updated successfully!");
}

main()
    .catch(e => console.error(e))
    .finally(async () => {
        await prisma.$disconnect();
    });
