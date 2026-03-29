import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
    try {
        const count = await prisma.agent.count();
        console.log(`Found ${count} agents in the database.`);

        if (count > 0) {
            const agents = await prisma.agent.findMany();
            console.log('Agents:', JSON.stringify(agents, null, 2));
        } else {
            console.log('No agents found. Checking if we can recover from backup or if Skill table exists (via raw query)...');
            try {
                // Try to query the old table name directly if it exists
                const oldSkills = await prisma.$queryRaw`SELECT * FROM "Skill"`;
                console.log('Found data in old "Skill" table:', oldSkills);
            } catch {
                console.log('Old "Skill" table does not exist or cannot be accessed.');
            }
        }
    } catch (e) {
        console.error(e);
    } finally {
        await prisma.$disconnect();
    }
}

main();
