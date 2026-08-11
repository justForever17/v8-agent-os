const PUBLIC_RPA_LIBRARIES = Object.freeze([
    "RPA.Windows",
    "RPA.Browser.Selenium",
    "RPA.Excel.Files",
]);

export function projectPublicRpaAvailability(payload) {
    const raw = payload && typeof payload === "object" ? payload : {};
    const rawLibraries = raw.libraries && typeof raw.libraries === "object"
        ? raw.libraries
        : {};
    return {
        robotFramework: raw.robotFramework === true,
        rpaFramework: raw.rpaFramework === true,
        libraries: Object.fromEntries(
            PUBLIC_RPA_LIBRARIES.map((name) => [name, rawLibraries[name] === true]),
        ),
    };
}
