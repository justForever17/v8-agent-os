
---

## 1️⃣ **ConvoMem / MemBench**

- **官方 GitHub**：  
    [https://github.com/allenai/convomem](https://github.com/allenai/convomem)
    
- **Hugging Face 数据集**：  
    [https://huggingface.co/datasets/convomem](https://huggingface.co/datasets/convomem)
    
- **参与指南**：
    
    1. 克隆 GitHub 仓库或下载 Hugging Face 数据集。
        
    2. 使用你自己的 agent 模型生成多轮对话预测。
        
    3. 使用提供的 `evaluation.py` 计算记忆召回率和一致性指标。
        
    4. 可将结果提交至官方 leaderboard（如支持）。
        
- **关键指标**：
    
    - 信息回忆率（Recall of past info）
        
    - 对话一致性评分（Consistency Score）
        

---

## 2️⃣ **ALFWorld / ALFRED extension**

- **官方 GitHub**：  
    [https://github.com/alfworld/alfworld](https://github.com/alfworld/alfworld)
    
- **Hugging Face 数据集**（ALFRED v1.1）：  
    [https://huggingface.co/datasets/alfred](https://huggingface.co/datasets/alfred)
    
- **参与指南**：
    
    1. 安装依赖并运行 ALFWorld 仿真环境。
        
    2. 使用你的 agent 控制虚拟机器人完成任务（如“拿到书并放到桌子上”）。
        
    3. 仓库中提供了 `eval_task.py` 脚本计算：
        
        - 成功率（Task Success Rate）
            
        - 步骤记忆准确率（Step Recall Accuracy）
            
    4. 可以在 GitHub 或社区提交实验结果与 baseline 对比。
        
- **关键指标**：
    
    - 完成率（Success Rate）
        
    - 步骤记忆准确率（Step Recall）
        
    - 任务执行一致性
        

---

## 3️⃣ **LoCoMo**

- **官方 GitHub**：  
    [https://github.com/facebookresearch/locomo](https://github.com/facebookresearch/locomo)
    
- **Hugging Face 数据集**（部分数据集迁移）：  
    [https://huggingface.co/datasets/locomo](https://huggingface.co/datasets/locomo)
    
- **参与指南**：
    
    1. 下载数据集或直接在 Hugging Face 上加载：
        
        ```python
        from datasets import load_dataset
        dataset = load_dataset("locomo")
        ```
        
    2. 使用你的 agent 对长上下文对话进行预测。
        
    3. 运行官方 `evaluation` 脚本计算：
        
        - 回忆率（Recall）
            
        - 一致性（Consistency）
            
    4. 可提交结果到官方 leaderboard 或内部 benchmark。
        
- **关键指标**：
    
    - 信息回忆率
        
    - 对话一致性
        
    - 长文本推理正确率
        

---

💡 **小贴士**：

- 如果你的 agent 是基于 LLM（如 GPT-4、LLaMA、MPT 等），可以结合 **RAG（Retrieval-Augmented Generation）** 或**长期记忆模块**提升在这些 benchmark 上的表现。
    
- 推荐先在 Hugging Face 数据集上进行快速本地测试，再在官方 benchmark 提交结果。
    

---
