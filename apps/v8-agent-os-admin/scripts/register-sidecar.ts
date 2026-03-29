
import { PrismaClient } from '@prisma/client'

const prisma = new PrismaClient()

async function main() {
    console.log('Registering Skill Sidecar MCP Server...')

    const config = JSON.stringify({
        type: 'sse',
        url: 'http://localhost:9530/sse'
    })

    const tool = await prisma.mCPTool.upsert({
        where: {
            name: 'skill-sidecar'
        },
        update: {
            config: config,
            isEnabled: true
        },
        create: {
            name: 'skill-sidecar',
            description: 'Python Sidecar for executing Universal Agent Skills (Docker Sandbox)',
            config: config,
            isEnabled: true
        }
    })

    console.log('Registered MCP Tool:', tool)
}

main()
    .catch((e) => {
        console.error(e)
        process.exit(1)
    })
    .finally(async () => {
        await prisma.$disconnect()
    })
