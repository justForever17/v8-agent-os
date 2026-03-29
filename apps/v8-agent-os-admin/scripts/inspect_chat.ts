import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
    const conversationId = 'cmjh5pipn000v3lhrvv0dhwdb'; // User provided ID
    console.log(`Inspecting conversation: ${conversationId}`);

    // Try to find as conversation ID
    const conversation = await prisma.conversation.findUnique({
        where: { id: conversationId },
        include: {
            messages: {
                orderBy: { createdAt: 'asc' }
            }
        }
    });

    if (conversation) {
        console.log('Found Conversation!');
        console.log(JSON.stringify(conversation, null, 2));
        return;
    }

    console.log('Conversation not found with that ID. Checking if it is a user ID or other...?');
    // It might be a message ID?
    const message = await prisma.message.findUnique({
        where: { id: conversationId }
    });

    if (message) {
        console.log('Found Message with that ID!');
        console.log(JSON.stringify(message, null, 2));
        return;
    }

    console.log('No conversation or message found with that ID.');

}

main()
    .catch((e) => {
        console.error(e);
        process.exit(1);
    })
    .finally(async () => {
        await prisma.$disconnect();
    });
