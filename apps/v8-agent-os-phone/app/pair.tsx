import { Redirect, useLocalSearchParams, type Href } from "expo-router";

export default function PairDeviceRoute() {
    const params = useLocalSearchParams<{
        admin?: string;
        code?: string;
        instance?: string;
        server?: string;
        surface?: string;
        manifest?: string;
    }>();
    const query = new URLSearchParams();
    if (params.admin) query.set("admin", params.admin);
    if (params.code) query.set("code", params.code);
    if (params.instance) query.set("instance", params.instance);
    if (params.server) query.set("server", params.server);
    if (params.surface) query.set("surface", params.surface);
    if (params.manifest) query.set("manifest", params.manifest);
    const pairingUri = `v8agentosphone://pair?${query.toString()}`;
    return (
        <Redirect
            href={{
                pathname: "/login",
                params: { pairingUri },
            } as Href}
        />
    );
}

