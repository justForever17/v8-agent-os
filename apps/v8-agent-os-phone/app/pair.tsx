import { Redirect, useLocalSearchParams, type Href } from "expo-router";

export default function PairDeviceRoute() {
    const params = useLocalSearchParams<{
        admin?: string;
        code?: string;
        instance?: string;
        surface?: string;
    }>();
    const query = new URLSearchParams();
    if (params.admin) query.set("admin", params.admin);
    if (params.code) query.set("code", params.code);
    if (params.instance) query.set("instance", params.instance);
    if (params.surface) query.set("surface", params.surface);
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

