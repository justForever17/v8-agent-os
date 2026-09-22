// The unified Web host owns the only NextAuth instance and session cookie.
// Admin server routes import this compatibility path so they cannot drift back
// to the removed standalone Admin session.
export { auth, handlers, signIn, signOut } from "@/lib/auth";
