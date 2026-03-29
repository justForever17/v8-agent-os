// eslint-disable-next-line @typescript-eslint/no-require-imports
const { PrismaClient } = require('@prisma/client');
const prisma = new PrismaClient();

async function main() {
    const id = 'cmi9xg4hc0001w1uh88wtig4i';
    console.log(`Attempting to delete conversation: ${id}`);

    try {
        const conversation = await prisma.conversation.findUnique({
            where: { id },
        });

        if (!conversation) {
            console.log('Conversation not found.');
        } else {
            console.log('Found conversation:', conversation);
            await prisma.message.deleteMany({
                where: { conversationId: id },
            });
            console.log('Deleted associated messages.');

            await prisma.conversation.delete({
                where: { id },
            });
            console.log('Successfully deleted conversation.');
        }
    } catch (e) {
        console.error('Error deleting conversation:', e);
    } finally {
        await prisma.$disconnect();
    }
}

main();
