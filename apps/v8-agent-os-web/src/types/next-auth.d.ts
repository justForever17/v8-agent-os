import "next-auth";
import "next-auth/jwt";

declare module "next-auth" {
    interface Session {
        user: {
            id: string;
            email?: string | null;
            name?: string | null;
            image?: string | null;
            login?: string;
            role?: string;
        };
    }

    interface User {
        login?: string;
        role?: string;
    }
}

declare module "next-auth/jwt" {
    interface JWT {
        id?: string;
        login?: string;
        role?: string;
    }
}
