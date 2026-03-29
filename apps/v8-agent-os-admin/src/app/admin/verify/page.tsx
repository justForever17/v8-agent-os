import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Mail } from "lucide-react"

export default function VerifyRequest() {
    return (
        <div className="flex min-h-screen items-center justify-center bg-muted/10">
            <Card className="w-full max-w-md text-center">
                <CardHeader>
                    <div className="mx-auto w-12 h-12 bg-primary/10 rounded-full flex items-center justify-center mb-4">
                        <Mail className="w-6 h-6 text-primary" />
                    </div>
                    <CardTitle>检查您的邮箱</CardTitle>
                    <CardDescription>
                        我们已向您发送了一个登录链接。<br />
                        请点击邮件中的链接以完成登录。
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <p className="text-sm text-muted-foreground">
                        如果您没有收到邮件，请检查垃圾邮件文件夹。
                    </p>
                </CardContent>
            </Card>
        </div>
    )
}
