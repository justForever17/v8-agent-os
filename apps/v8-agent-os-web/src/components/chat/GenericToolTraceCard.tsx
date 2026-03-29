import React from 'react';
import { motion } from 'framer-motion';
import { ToolCard, ToolInvocation } from './ToolCard';

interface GenericToolTraceCardProps {
    toolInvocation: ToolInvocation;
}

export function GenericToolTraceCard({ toolInvocation }: GenericToolTraceCardProps) {
    return (
        <motion.div 
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 25 }}
            layout
            className="opacity-60 hover:opacity-100 transition-opacity origin-left"
        >
            <ToolCard toolInvocation={toolInvocation} />
        </motion.div>
    );
}
