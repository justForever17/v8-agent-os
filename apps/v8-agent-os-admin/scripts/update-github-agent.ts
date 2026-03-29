
import { PrismaClient } from '@prisma/client'

const prisma = new PrismaClient()

async function main() {
    console.log('Updating Agent: Github公众号推文...')

    const agentId = 'cmjvlox2t00021u4sqs56nt3m' // Found via list-agents.ts

    const agent = await prisma.agent.findUnique({
        where: { id: agentId }
    })

    if (!agent) {
        console.error('Agent not found!')
        process.exit(1)
    }

    // Define the Universal Computer Tools
    const sidecarTools = [
        'execute_command',
        'read_file',
        'write_file',
        'manage_skills'
    ]

    // Merge with existing tools if any (assuming JSON array of strings)
    const existingTools = (agent.tools as string[]) || []
    const newTools = Array.from(new Set([...existingTools, ...sidecarTools]))

    // Append "Learner" Instructions to System Prompt
    const learnerInstructions = `
\n\n# Tool Capability: The Universal Computer
You have access to a secure computer sandbox via the "v8chat-computer" tools.
- \`execute_command\`: Run bash commands.
- \`manage_skills\`: Discover and learn new skills.

## How to use Skills
You are not pre-programmed with every skill. instead, you must **LEARN** them dynamically.
1. **Search**: If the user asks for a task (e.g., "Bundle HTML"), use \`manage_skills(action="list")\` to find relevant skills.
2. **Learn**: Use \`manage_skills(action="inspect", skill_name="...")\` to read the skill's manual (SKILL.md).
3. **Execute**: Follow the manual's instructions to execute scripts using \`execute_command\`.

## Filesystem Rules
- Your code scripts are in \`/app/skills\` (Read-Only).
- Your workspace is \`/share\` (Read-Write). This maps to the public web folder.
- Always output files to \`/share\` so the user can see them.
`

    // Prevent double appending
    let newPrompt = agent.systemPrompt || ""
    if (!newPrompt.includes("Tool Capability: The Universal Computer")) {
        newPrompt += learnerInstructions
    }

    await prisma.agent.update({
        where: { id: agentId },
        data: {
            tools: newTools,
            systemPrompt: newPrompt
        }
    })

    console.log('Successfully updated Agent tools and prompt.')
}

main()
    .catch(console.error)
    .finally(() => prisma.$disconnect())
