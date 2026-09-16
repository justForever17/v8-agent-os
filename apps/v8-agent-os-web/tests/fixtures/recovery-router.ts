export function useRouter() {
    return { push: (url: string) => { (window as any).lastNavigation = url; } };
}
