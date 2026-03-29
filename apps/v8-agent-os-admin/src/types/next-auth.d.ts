import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
    interface Session {
        user: {
            id: string;
            email?: string | null;
            name?: string | null;
            image?: string | null;
            role?: string;
            login?: string;
            mustChangePassword?: boolean;
        };
    }

    interface User {
        role?: string;
        login?: string;
        mustChangePassword?: boolean;
    }
}

declare module "next-auth/jwt" {
    interface JWT {
        role?: string;
        login?: string;
        mustChangePassword?: boolean;
    }
}
