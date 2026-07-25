export function shouldUseSecureCookies(): boolean {
    const publicOrigin = String(process.env.AUTH_URL || process.env.NEXTAUTH_URL || "").trim();
    return publicOrigin.toLowerCase().startsWith("https://");
}
