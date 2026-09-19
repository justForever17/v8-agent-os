const assert = require('node:assert/strict');
const test = require('node:test');
const { loadHook, tick, deferred } = require('./helpers/hook-harness.cjs');

// Deterministic component/event tests. Native hit testing, layout and real React
// rendering are checked separately by phone-navigation-ui.py.
function harness(mutate = source => source) {
    const pending = [], routes = [];
    let focused = true, visible = true, flushCount = 0;
    const jsx = (type, props) => ({ type, props });
    const hook = loadHook('src/components/layout/PhoneNavigationMenu.tsx', {
        'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' },
        'react-native': { ActivityIndicator: 'Spinner', Modal: 'Modal', Platform: { OS: 'android' }, Pressable: 'Pressable', ScrollView: 'ScrollView', Text: 'Text', View: 'View', StyleSheet: { create: value => value } },
        'react-native-safe-area-context': { SafeAreaView: 'SafeAreaView' },
        '@react-navigation/native': { useIsFocused: () => focused },
        'expo-router': { router: { navigate: path => routes.push(['navigate', path]), dismissTo: path => routes.push(['dismissTo', path]) } },
        'lucide-react-native': { Menu: 'Menu', X: 'X' },
        '@/src/components/chat/ProfileMenuOverlay': { ProfileMenuOverlay: 'Profile' },
        '@/src/components/rpa/PhoneRpaOverlay': { PhoneRpaOverlay: 'Rpa' },
        '@/src/hooks/use-app-visibility': { useAppVisibility: () => visible },
        '@/src/lib/phone-drafts': { phoneDrafts: { flushAll: () => { flushCount++; const task = deferred(); pending.push(task); return task.promise; } } },
        '@/src/providers/app-session': { useAppSession: () => ({ authorityKey: 'A', servingInstanceId: 'engine-A', status: 'authenticated' }) },
        '@/src/providers/ui-prefs': { useUiPrefs: () => ({ colors: {}, t: key => key }) },
    }, source => mutate(source) + '\nexport { NavigationMenu };');
    let tree;
    const render = () => { tree = hook.render(module => module.NavigationMenu()); };
    const nodes = (type, root = tree) => {
        if (!root || typeof root !== 'object') return [];
        if (Array.isArray(root)) return root.flatMap(item => nodes(type, item));
        if (root.type === 'Modal' && !root.props.visible) return [];
        return [...(root.type === type ? [root] : []), ...nodes(type, root.props?.children ?? null)];
    };
    const press = label => {
        const button = nodes('Pressable').find(node => node.props.accessibilityLabel === label
            || nodes('Text', node).some(text => text.props.children === label));
        assert.ok(button, `missing button ${label}`);
        if (!button.props.disabled) button.props.onPress();
        render();
    };
    render();
    return { pending, routes, render, press, nodes, hook, get flushCount() { return flushCount; },
        focus: next => { focused = next; render(); render(); },
        visibility: next => { visible = next; render(); render(); } };
}
const menu = 'phone.devices.navigation', settings = 'phone.devices.settings', devices = 'phone.devices.title';
const close = 'phone.navigation.close';

test('rapid selections save once, stay open during save, and navigate only after success', async () => {
    const h = harness(); h.press(menu); h.press(settings); h.press(devices);
    assert.equal(h.flushCount, 1); assert.equal(h.routes.length, 0);
    assert.equal(h.nodes('Modal').length, 1);
    assert.ok(h.nodes('Pressable').filter(n => n.props.disabled).length >= 4);
    h.pending[0].resolve(); await tick(); h.render();
    assert.deepEqual(h.routes, [['navigate', '/settings']]);
    assert.equal(h.nodes('Modal').length, 0);
});

for (const boundary of ['close', 'native-request-close', 'blur', 'background', 'unmount']) {
    test(`${boundary} invalidates an in-flight navigation`, async () => {
        const h = harness(); h.press(menu); h.press(settings);
        if (boundary === 'close') h.press(close);
        if (boundary === 'native-request-close') h.nodes('Modal')[0].props.onRequestClose();
        if (boundary === 'blur') h.focus(false);
        if (boundary === 'background') h.visibility(false);
        if (boundary === 'unmount') h.hook.unmount();
        h.pending[0].resolve(); await tick();
        assert.deepEqual(h.routes, []);
    });
}

test('failure stays visible, retry clears the error, and returning to chat dismisses', async () => {
    const h = harness(); h.press(menu); h.press(settings);
    h.pending[0].reject(new Error('disk full')); await tick(); h.render();
    assert.ok(h.nodes('Text').some(n => n.props.children === 'disk full'));
    assert.deepEqual(h.routes, []);
    h.press('phone.devices.returnChat');
    assert.ok(!h.nodes('Text').some(n => n.props.children === 'disk full'));
    h.pending[1].resolve(); await tick(); h.render();
    assert.deepEqual(h.routes, [['dismissTo', '/chat']]);
});

test('a cancelled save cannot close or navigate the reopened menu', async () => {
    const h = harness(); h.press(menu); h.press(settings); h.press(close); h.press(menu); h.press(devices);
    h.pending[0].reject(new Error('old error')); await tick(); h.render();
    assert.ok(!h.nodes('Text').some(n => n.props.children === 'old error'));
    assert.equal(h.nodes('Modal').length, 1);
    h.pending[1].resolve(); await tick(); h.render();
    assert.deepEqual(h.routes, [['navigate', '/connect']]);
});

for (const [label, component] of [['src.screens.rpascreen.title', 'Rpa'], ['src.components.chat.profilemenuoverlay.profile_center', 'Profile']]) {
    test(`${component} opens its original surface and closes without a route change`, async () => {
        const h = harness(); h.press(menu); h.press(label);
        h.pending[0].resolve(); await tick(); h.render();
        assert.equal(h.nodes(component).length, 1);
        h.nodes(component)[0].props.onClose(); h.render();
        assert.equal(h.nodes(component).length, 0);
        assert.deepEqual(h.routes, []);
    });
}

test('cancellation oracle rejects a mutant with its post-save ticket check removed', async () => {
    const h = harness(source => source.replace('if (request.current !== ticket) return;', ''));
    h.press(menu); h.press(settings); h.press(close);
    h.pending[0].resolve(); await tick();
    assert.deepEqual(h.routes, [['navigate', '/settings']], 'mutant must reproduce the unwanted late navigation');
});
