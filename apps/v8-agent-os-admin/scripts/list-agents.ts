
import { PrismaClient } from '@prisma/client'

const prisma = new PrismaClient()

async function main() {
    const agents = await prisma.agent.findMany()
    console.log('--- Agents List ---')
    agents.forEach(a => {
        console.log(`[${a.id}] ${a.name}`)
    })
}

main()
    .catch(console.error)
    .finally(() => prisma.$disconnect())
