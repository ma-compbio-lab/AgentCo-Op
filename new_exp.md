我觉得现在的workflow有下面几个问题需要改进和修复：
- 关于workflow的dynamic design的问题，现在的workflow是下面这个样子的：
  1. 先选一个预定义的 blueprint 模板
  2. 把具体 task 信息注入这个 blueprint
  3. 运行时按 gate 条件激活可选 subgraph
  4. 失败时可选地对 graph 做 patch / repair
这样不是“给一个任务后从零自动搜索并生成任意 graph”，这样的情况下是不是遇到一种新的类型的benchmark就无法
找到对应的blueprint了？我希望你改进这一点，在没有找到pre defined的graph的时候保留“给一个任务后从零自动搜索并生成任意 graph”的能力；

- 现在你pre defined的blueprint是基于一些特定的benchmark设计的，这样就导致了上面的问题，我觉得你应该改成基于一些更general的task design来设计pre defined blueprint，这样就能覆盖更多类型的任务；你需要注意的是，workflow不能只针对某一个实验来设计和实现，而是要有一定的通用性和适应性，能够支持不同类型的实验和任务，所以在设计和实现workflow的时候，你需要考虑到这些实验的需求和特点，来设计一个既能满足这些实验的需求又具有一定通用性的workflow，这样才能保证workflow的实用性和有效性；

- 另外，我觉得现在的workflow在运行时的灵活性也有待提升，比如说在运行过程中如果遇到一些意外情况或者新的需求，能否动态地调整workflow的结构或者添加新的subgraph来应对这些变化？我希望你能考虑增加这种动态调整的能力，这样可以让workflow更加适应不同的任务和环境；

- 我觉得你应该增加一些监控和反馈机制来评估workflow的性能和效果，这样可以帮助你更好地了解workflow在实际应用中的表现，并且根据反馈不断优化和改进workflow的设计和实现。  

- 现在你predefined blueprint的设计如下：
      - MATH v2：solver + programmer -> program_exec -> selector -> [reviewer -> reviser]
        见 math_v2.yaml:48
      - HumanEval v2：coder -> public_test_runner -> [reviewer -> reviser -> retest_runner]
        见 humaneval_v2.yaml:37
      - MedQA：planner -> responder -> [reviewer -> reviser]
  
  我觉得你需要参考更多的相关资料和问下，设计一些更好的predefined blueprint,现在的你设计的这个blueprint在medqa上的表现连baseline都不如，我之前的经验是在MedQA这种不是很困难的任务上，甚至single step的solver就能达到不错的效果，所以我觉得你设计的这个predefined blueprint可能不太适合MedQA这个任务，你可以考虑重新设计一下这个predefined blueprint，或者参考一些其他的资料和问一下，看看有没有更好的设计方案。

- 在workflow中动态设计graph的时候，你应该需要去搜索现有的github repo/资料，看看什么样是最合适的，就像tool search一样，你可以设计一个graph search的模块，来搜索和评估不同的graph结构，找到最适合当前任务的graph，这样就能更好地利用现有的资源和知识，提高workflow的性能和效果。

- 完成上面的功能之后，你需要重构清理codebase，把一些重复的代码和不必要的代码删除掉，保持codebase的整洁和可维护性，这样也能提高开发效率和代码质量。

- 对于现在的实验部分，我觉得有下面的问题：
  1. Experiment 1 & 2问题不是很大，但是我希望把Experiment 1 MedQA的base model改为GPT 5 nano；
  2. Experiment 3的问题比较多；我现在要重新设计Experiment 3, 现在这几个实验全部太过于naive了；

- 重新设计的Experiment 3:
  1. Closed-loop Spatial Panel Design and Validation. 详细信息在`experiment3-1.md`中
  2. 论文数据集transfer task: cell2location repo transfer，详细信息在`experiment3-2.md`中
  3. Specialized agent collaboration for complex tasks: Spatially Grounded CRISPR Screen Design for Tumor Immune Reactivation, 详细信息在`experiment3-3.md`中
  4. Experiment 3还是使用GPT 5 Thinking作为base model，来评估改进之后的workflow在这些更复杂的实验中的表现；
  5. 把之前experiment 3留下的一些不太好的设计和实现的部分删除掉，保持实验的质量和效果；

- experiments 3可能需要agent workflow的改进和增强/更多的feature功能才能更好地支持这些实验的设计和实现，所以你需要先完成上面关于workflow的改进和增强，然后再来实现运行这些实验，这样才能保证实验的质量和效果；

- 重新写`experiments.md`，把过时的日志删除，把新的实验设计写上去，保持这个文件的整洁和可读性。还要把改进之后的workflow设计和实现写上去，保持这个文件的完整性和系统性。

- 在完成对codebase相应功能的改进和增强之后，你需要重新运行这些实验，收集新的数据和结果，并且分析和总结这些结果，看看改进之后的workflow在这些实验中的表现如何，有没有达到预期的效果，如果没有的话，看看是什么原因导致的，然后根据分析的结果继续优化和改进workflow的设计和实现，不断提升workflow的性能和效果。