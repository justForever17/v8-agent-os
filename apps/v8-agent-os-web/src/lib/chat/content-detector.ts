/* eslint-disable @typescript-eslint/no-explicit-any */
export type ContentBlockType =
    | 'thinking' | 'tool' | 'artifact' | 'voice'  // Special tags
    | 'code' | 'mermaid'                 // Code blocks
    | 'html_snippet'                  // HTML code blocks
    | 'model-3d'                      // 3D Model URLs
    | 'file-ppt'                      // PPT File Links
    | 'file-html'                     // HTML File Links
    | 'text';                         // Standard text

export interface ContentBlock {
    id: string;
    type: ContentBlockType;
    content: string;
    isStreaming?: boolean;
    data?: any;
}


/**
 * Content Detector & Parser
 * Handles:
 * 1. Special Tags: <think>, <tool-call>, <artifact>, <voice>
 * 2. Markdown Code Blocks: ```lang ... ``` (including unclosed blocks in streaming)
 * 3. Text
 */
export function parseContentToBlocks(
    content: string,
    isStreaming: boolean,
    startId: number,
    parseInlineThinking = true,
): ContentBlock[] {
    const blocks: ContentBlock[] = [];
    let blockIndex = startId;
    let processedContent = content;

    // 0. Streaming Safety Check: Remove incomplete tags at the very end of the stream
    if (isStreaming) {
        // Match partial tags like <thi, <tool, <artif, <voi at the end of string
        const incompleteTagMatch = processedContent.match(/<(?:t|th|thi|thin|think|to|too|tool|a|ar|art|arti|artif|artifa|artifac|artifact|v|vo|voi|voic|voice|\/|\/t|\/th|\/thi|\/thin|\/think|\/a|\/ar|\/art|\/arti|\/artif|\/artifac|\/artifact|\/v|\/vo|\/voi|\/voic|\/voice)[^>]*$/i);
        // Also match incomplete attributes like <tool-call id="
        const incompleteAttrMatch = processedContent.match(/<(?:tool-call|artifact|voice)[^>]+$/i);

        if (incompleteTagMatch) {
            processedContent = processedContent.slice(0, incompleteTagMatch.index);
        } else if (incompleteAttrMatch) {
            // Only if it doesn't end with >
            if (!incompleteAttrMatch[0].endsWith('>')) {
                processedContent = processedContent.slice(0, incompleteAttrMatch.index);
            }
        }
    }

    // A. First split by Special Tags (High Priority)
    // Using a regex that captures the delimiters to preserve them in the split/match

    // Regex explanation:
    // 1. <think>...</think> (or unclosed <think>...)
    // 2. <tool-call ... />
    // 3. <artifact ...>...</artifact> (or unclosed)
    // 4. <voice>...</voice> (or unclosed)
    const splitRegex = /(<think(?:>|\s+[^>]*>))([\s\S]*?)(?:<\/think>|$)|(<tool-call id="([^"]+)"\s*\/>)|(<artifact\s+(?:[^>]*?)>)([\s\S]*?)(?:<\/artifact>|$)|(<voice(?:>|\s+[^>]*>))([\s\S]*?)(?:<\/voice>|$)/g;

    let lastIndex = 0;
    let match;

    // Helper to process text content for code blocks
    const processTextBlock = (text: string, isLastBlock: boolean) => {
        if (!text) return;

        // Code Block Detection (Markdown Code Fences)
        const codeBlockRegex = /```(\w*)(?:\n| )([\s\S]*?)(?:```|$)/g;
        let lastTextIndex = 0;
        let codeMatch;

        while ((codeMatch = codeBlockRegex.exec(text)) !== null) {
            // Text before code block
            if (codeMatch.index > lastTextIndex) {
                const preText = text.substring(lastTextIndex, codeMatch.index);
                if (preText) blocks.push({ id: `text-${blockIndex++}`, type: 'text', content: preText });
            }

            const lang = (codeMatch[1] || 'text').trim().toLowerCase();
            const codeContent = codeMatch[2];
            const isUnclosed = !codeMatch[0].endsWith('```');

            // Streaming Optimization:
            // If it's the last block of the entire content AND it's streaming AND it's unclosed
            // We might want to hold back if it's potentially incomplete logic
            // But for now, we just mark it as streaming so the renderer knows

            // Special handling for Mermaid
            if (lang === 'mermaid') {
                blocks.push({
                    id: `mermaid-${blockIndex++}`,
                    type: 'mermaid',
                    content: codeContent,
                    isStreaming: isStreaming && isUnclosed
                });
            } else if (lang === 'html' || lang === 'xml') {
                blocks.push({
                    id: `html-${blockIndex++}`,
                    type: 'html_snippet',
                    content: codeContent,
                    isStreaming: isStreaming && isUnclosed,
                    data: { language: lang }
                });
            } else {
                blocks.push({
                    id: `code-${blockIndex++}`,
                    type: 'code',
                    content: codeContent,
                    isStreaming: isStreaming && isUnclosed,
                    data: { language: lang }
                });
            }

            lastTextIndex = codeBlockRegex.lastIndex;
        }

        // CUSTOM: Detect standalone 3D model links in text (e.g. http://.../model.glb)
        // This is a simple heuristic: if a text block contains ONLY a model URL, or we want to split it out
        // For now, let's scan remaining text for http...glb/gltf links and split them into 'model-3d' blocks
        if (lastTextIndex < text.length) {
            const remaining = text.substring(lastTextIndex);
            // Regex for model URLs: matches http/https ending in .glb or .gltf
            // We only match if it's "mostly" the whole line or clearly separated, but for simplicity let's find all occurrences
            const modelUrlRegex = /(https?:\/\/[^\s[\])]+\.(?:glb|gltf))/gi;

            let modelMatch;
            let lastInnerIndex = 0;
            const innerBlocks: ContentBlock[] = [];

            while ((modelMatch = modelUrlRegex.exec(remaining)) !== null) {
                // Text before model link
                if (modelMatch.index > lastInnerIndex) {
                    const pre = remaining.substring(lastInnerIndex, modelMatch.index);
                    if (pre.trim()) innerBlocks.push({ id: `text-${blockIndex++}`, type: 'text', content: pre });
                }

                // The 3D Model Link
                innerBlocks.push({
                    id: `model-${blockIndex++}`,
                    type: 'model-3d',
                    content: modelMatch[1], // The URL
                    isStreaming: false
                });

                lastInnerIndex = modelUrlRegex.lastIndex;
            }

            // Text after last model link
            if (lastInnerIndex < remaining.length) {
                const post = remaining.substring(lastInnerIndex);
                // Streaming Safety: checks for partial fence at end
                if (isStreaming && isLastBlock) {
                    const partialFence = post.match(/```\w*$/);
                    if (partialFence) {
                        const safe = post.slice(0, partialFence.index);
                        if (safe) innerBlocks.push({ id: `text-${blockIndex++}`, type: 'text', content: safe });
                    } else {
                        innerBlocks.push({ id: `text-${blockIndex++}`, type: 'text', content: post });
                    }
                } else {
                    innerBlocks.push({ id: `text-${blockIndex++}`, type: 'text', content: post });
                }
            }

            // If we found models, add them all. If not, fallback to original logic (handled by keeping innerBlocks empty)
            if (innerBlocks.length > 0) {
                // Push all inner blocks to main blocks
                // NOTE: We deviate from original logic's "push remaining" here
                // To avoid double pushing, we simply don't fall through to the checks below
                blocks.push(...innerBlocks);
                return;
            }
        }

        // Remaining text after last code block
        if (lastTextIndex < text.length) {
            const remaining = text.substring(lastTextIndex);
            // Streaming Safety: If we are streaming and at the end, checks for incomplete ``` code fence start
            if (isStreaming && isLastBlock) {
                const partialFence = remaining.match(/```\w*$/);
                if (partialFence) {
                    // Hide the partial fence until we get language or newline
                    const safeContent = remaining.slice(0, partialFence.index);
                    if (safeContent) blocks.push({ id: `text-${blockIndex++}`, type: 'text', content: safeContent });
                    return;
                }
            }
            blocks.push({ id: `text-${blockIndex++}`, type: 'text', content: remaining });
        }
    };


    while ((match = splitRegex.exec(processedContent)) !== null) {
        // 1. Content BEFORE the special tag
        if (match.index > lastIndex) {
            const textContent = processedContent.substring(lastIndex, match.index);
            processTextBlock(textContent, false);
        }

        // 2. The Special Tag itself
        if (match[1]) {
            // --- Thinking (<think>) ---
            const openingTag = match[1];
            const thinkContent = match[2];
            const isIncomplete = !match[0].endsWith('</think>'); // captured by the regex group strictness or lack thereof with |$

            // Extract attributes if any 
            let elapsedTime: number | undefined;
            const timeMatch = openingTag.match(/time="(\d+)"/);
            if (timeMatch) elapsedTime = parseInt(timeMatch[1], 10);

            if (parseInlineThinking) {
                blocks.push({
                    id: `think-${blockIndex++}`,
                    type: 'thinking',
                    content: thinkContent,
                    isStreaming: isStreaming && isIncomplete,
                    data: { startTime: Date.now(), elapsedTime } // elapsedTime can be updated by UI
                });
            }

        } else if (match[3]) {
            // --- Tool Call (<tool-call>) ---
            const toolId = match[4];
            blocks.push({
                id: `tool-marker-${toolId}`,
                type: 'tool',
                content: 'Tool Call',
                data: { toolCallId: toolId }
            });

        } else if (match[5]) {
            // --- Artifact (<artifact>) ---
            const openingTag = match[5];
            const artifactContent = match[6];
            const titleMatch = openingTag.match(/title="([^"]+)"/);
            const typeMatch = openingTag.match(/type="([^"]+)"/);

            blocks.push({
                id: `artifact-${blockIndex++}`,
                type: 'artifact',
                content: artifactContent,
                data: {
                    title: titleMatch ? titleMatch[1] : 'Artifact',
                    type: typeMatch ? typeMatch[1] : 'html'
                }
            });
        } else if (match[7]) {
            // --- Voice (<voice>) ---
            const voiceContent = match[8];
            const isIncomplete = !match[0].endsWith('</voice>');
            blocks.push({
                id: `voice-${blockIndex++}`,
                type: 'voice',
                content: voiceContent,
                isStreaming: isStreaming && isIncomplete
            });
        }

        lastIndex = splitRegex.lastIndex;
    }

    // 3. Remaining content after last specal tag
    if (lastIndex < processedContent.length) {
        const textContent = processedContent.substring(lastIndex);
        processTextBlock(textContent, true);
    }

    // 4. POST-PROCESSING: Extract PPT and HTML links from text blocks
    const finalBlocks: ContentBlock[] = [];
    blocks.forEach(b => {
        if (b.type !== 'text') { finalBlocks.push(b); return; }

        let tempBlocks: ContentBlock[] = [];

        // PPT Links
        const pptRegex = /\[([^\]]+)\]\(([^)]+\.pptx?(?:\?[^)]*)?)\)/g;
        let pptLastIdx = 0;
        let pptMatch;
        let pCount = 0;
        while ((pptMatch = pptRegex.exec(b.content)) !== null) {
            if (pptMatch.index > pptLastIdx) {
                tempBlocks.push({ ...b, id: `${b.id}-pptpre-${pCount}`, content: b.content.slice(pptLastIdx, pptMatch.index) });
            }
            tempBlocks.push({ id: `${b.id}-ppt-${pCount}`, type: 'file-ppt', content: pptMatch[0], data: { filename: pptMatch[1], url: pptMatch[2] } });
            pptLastIdx = pptRegex.lastIndex;
            pCount++;
        }
        if (pptLastIdx < b.content.length) {
            tempBlocks.push({ ...b, id: `${b.id}-pptpost`, content: b.content.slice(pptLastIdx) });
        }
        if (tempBlocks.length === 0) tempBlocks = [b];

        // HTML Links
        tempBlocks.forEach(tb => {
            if (tb.type !== 'text') { finalBlocks.push(tb); return; }
            const htmlRegex = /\[([^\]]+)\]\(([^)]+\.html(?:\?[^)]*)?)\)/g;
            let htmlLastIdx = 0;
            let htmlMatch;
            let hasHtml = false;
            let hCount = 0;
            while ((htmlMatch = htmlRegex.exec(tb.content)) !== null) {
                hasHtml = true;
                if (htmlMatch.index > htmlLastIdx) {
                    finalBlocks.push({ ...tb, id: `${tb.id}-htmlpre-${hCount}`, content: tb.content.slice(htmlLastIdx, htmlMatch.index) });
                }
                finalBlocks.push({ id: `${tb.id}-html-${hCount}`, type: 'file-html', content: htmlMatch[0], data: { filename: htmlMatch[1], url: htmlMatch[2] } });
                htmlLastIdx = htmlRegex.lastIndex;
                hCount++;
            }
            if (htmlLastIdx < tb.content.length) {
                if (hasHtml) {
                    finalBlocks.push({ ...tb, id: `${tb.id}-htmlpost`, content: tb.content.slice(htmlLastIdx) });
                } else {
                    finalBlocks.push(tb);
                }
            } else if (!hasHtml) {
                // Should not occur since length check handles it, but safe
            }
        });
    });

    return finalBlocks;
}
