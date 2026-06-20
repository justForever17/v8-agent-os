import { NextResponse } from "next/server";

export async function POST() {
    return NextResponse.json(
        {
            error: "公开注册已关闭。请在 Admin 完成 Owner 初始化，再通过设备配对连接。",
            nextAction: "pair_device",
        },
        { status: 410 },
    );
}
