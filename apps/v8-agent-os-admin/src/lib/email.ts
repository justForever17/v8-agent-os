import nodemailer from 'nodemailer';

const transporter = nodemailer.createTransport(process.env.EMAIL_SERVER);

export async function sendVerificationEmail(email: string, code: string) {
    if (!process.env.EMAIL_SERVER) {
        console.warn("EMAIL_SERVER is not defined. Skipping email sending.");
        console.log(`Verification code for ${email}: ${code}`);
        return;
    }

    try {
        await transporter.sendMail({
            from: process.env.EMAIL_FROM || 'noreply@example.com',
            to: email,
            subject: '您的验证码 - v8chat',
            text: `您的验证码是: ${code}\n该验证码在 10 分钟内有效。`,
            html: `
                <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
                    <h2>验证您的账户</h2>
                    <p>您好，</p>
                    <p>您正在注册或登录 v8chat。请输入以下验证码以继续：</p>
                    <div style="background-color: #f4f4f5; padding: 16px; border-radius: 8px; text-align: center; font-size: 24px; font-weight: bold; letter-spacing: 4px; margin: 20px 0;">
                        ${code}
                    </div>
                    <p>该验证码在 10 分钟内有效。如果您没有请求此验证码，请忽略此邮件。</p>
                </div>
            `,
        });
        console.log(`Email sent to ${email}`);
    } catch (error) {
        console.error("Failed to send email:", error);
        // In dev, log the code so we can still proceed
        if (process.env.NODE_ENV !== 'production') {
            console.log(`[DEV FALLBACK] Verification code for ${email}: ${code}`);
        }
        throw new Error("Failed to send verification email");
    }
}
