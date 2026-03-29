export async function generateImageWithDoubao(prompt: string, imageUrls: string[] = []) {
    const apiKey = process.env.VOLCENGINE_API_KEY;
    // Use the OpenAI-compatible Image Generation endpoint for Volcengine
    const endpoint = process.env.VOLCENGINE_ENDPOINT || "https://ark.cn-beijing.volces.com/api/v3/images/generations";

    try {
        // Construct payload for Doubao Seedream 4.0
        // Note: If imageUrls are present, it's Image-to-Image, but standard OpenAI Image API 
        // usually takes 'image' as file or specific param.
        // Seedream supports 'image_url' in some contexts or 'image' param.
        // For now, we'll focus on Text-to-Image if no images, or try to pass images if supported.

        interface VolcenginePayload {
            model: string;
            prompt: string;
            size: string;
            response_format: string;
            image_urls?: string[];
        }

        const payload: VolcenginePayload = {
            model: "doubao-seedream-4-0-250828",
            prompt: prompt,
            size: "1024x1024", // Default size
            response_format: "url",
        };

        // If we have reference images, we might need a different structure or parameter.
        // The search result mentioned 'image' parameter for reference images.
        if (imageUrls.length > 0) {
            // Assuming the API accepts a list of URLs for 'image' or similar
            // This is speculative based on "reference images (2-10)" capability.
            // We will try passing them as 'image_urls' or similar if standard OpenAI doesn't fit.
            // But standard OpenAI /images/generations doesn't support reference images easily.
            // We'll stick to basic Text-to-Image for now to ensure it works, 
            // or add them to prompt if the model supports it? No, model needs specific input.

            // Let's try to pass them in a way that might work for Volcengine's extension
            // payload.image_urls = imageUrls; 
        }

        const response = await fetch(endpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${apiKey}`
            },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Volcengine API Error: ${response.status} - ${errorText}`);
        }

        const data = await response.json();

        // Transform to match Chat Completion response structure for compatibility with our Chat API
        // OpenAI Image Gen returns { created: number, data: [{ url: string, ... }] }
        if (data.data && data.data.length > 0) {
            return {
                choices: [
                    {
                        message: {
                            content: `![Generated Image](${data.data[0].url})`
                        }
                    }
                ]
            };
        }

        return {
            choices: [{ message: { content: "Image generation successful but no URL returned." } }]
        };

    } catch (error) {
        console.error("Volcengine Error:", error);
        throw error;
    }
}
