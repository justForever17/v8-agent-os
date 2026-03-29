// Run from v8-agent-os-admin: npx tsx scripts/create-admin.ts
import * as bcrypt from 'bcryptjs';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import * as crypto from 'crypto';

const getBaseDir = () => path.join(os.homedir(), '.v8-agent-os');

async function main() {
    const email = 'admin@v8-agent-os.local';
    const password = 'admin';
    const name = 'Administrator';

    try {
        const baseDir = getBaseDir();
        if (!fs.existsSync(baseDir)) {
            fs.mkdirSync(baseDir, { recursive: true });
        }
        
        const usersFile = path.join(baseDir, 'users.json');
        if (!fs.existsSync(usersFile)) {
            fs.writeFileSync(usersFile, JSON.stringify({ users: [] }, null, 2));
        }

        const data = JSON.parse(fs.readFileSync(usersFile, 'utf-8'));
        if (!data.users) data.users = [];
        
        const existingUserIndex = data.users.findIndex((u: { email: string }) => u.email === email);
        const hashedPassword = await bcrypt.hash(password, 10);

        if (existingUserIndex >= 0) {
            data.users[existingUserIndex].password = hashedPassword;
            data.users[existingUserIndex].role = 'ADMIN';
            console.log(`✅ 密码已重置`);
            console.log(`   邮箱: ${email}`);
            console.log(`   密码: ${password}`);
        } else {
            data.users.push({
                id: crypto.randomUUID(),
                email,
                password: hashedPassword,
                name,
                role: 'ADMIN',
                emailVerified: new Date().toISOString(),
                createdAt: new Date().toISOString()
            });
            console.log(`✅ 管理员创建成功`);
            console.log(`   邮箱: ${email}`);
            console.log(`   密码: ${password}`);
        }
        
        fs.writeFileSync(usersFile, JSON.stringify(data, null, 2), 'utf-8');

    } catch (error) {
        console.error('❌ 失败:', error);
    }
}

main();
