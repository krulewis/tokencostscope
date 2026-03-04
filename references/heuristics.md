# Token Heuristics Reference

## Activity Token Estimates

| Activity            | Input Tokens | Output Tokens | Notes                                      |
|---------------------|--------------|---------------|--------------------------------------------|
| File read           | 10,000       | 200           | Typical 150-300 line source file           |
| File write (new)    | 1,500        | 4,000         | Planning context + generated code          |
| File edit           | 2,500        | 1,500         | Existing context + diff output             |
| Test write          | 2,000        | 5,000         | Test files are verbose                     |
| Code review pass    | 8,000        | 3,000         | Includes reading the diff/files            |
| Research/exploration| 5,000        | 2,000         | Search results + synthesis                 |
| Planning step       | 3,000        | 4,000         | Context gathering + plan output            |
| Grep/search         | 500          | 500           | Tool call overhead                         |
| Shell command       | 300          | 500           | Command + result                           |
| Conversation turn   | 5,000        | 1,500         | System prompt + tool definitions + response|

## Pipeline Step Activity Counts

N = file count from the implementation plan (e.g., 5 files → N=5).

| Step                  | Model  | Activities                                              |
|-----------------------|--------|---------------------------------------------------------|
| Research Agent        | Sonnet | 6 file reads, 4 searches, 1 planning step, 3 conv turns |
| Architect Agent       | Opus   | 1 code review pass, 1 planning step, 2 conv turns       |
| Engineer Initial Plan | Sonnet | 4 file reads, 2 searches, 1 planning step, 2 conv turns |
| Staff Review          | Opus   | 1 code review pass, 2 conv turns                        |
| Engineer Final Plan   | Sonnet | 2 file reads, 1 planning step, 2 conv turns             |
| Test Writing          | Sonnet | 3 file reads, N test writes, 3 conv turns               |
| Implementation        | Sonnet*| N file reads, N file edits, 4 conv turns                |
| Playwright QA         | Haiku  | 3 shell commands, 2 file reads, 2 conv turns            |

*Opus for L-size changes.

Note: Staff Review does NOT include separate file reads. The code review pass (8,000 input tokens)
already accounts for reading the diff and relevant files.

## Complexity Multipliers

| Complexity | Multiplier |
|------------|------------|
| Low        | 0.7x       |
| Medium     | 1.0x       |
| High       | 1.5x       |

Applied to both input and output base tokens before context accumulation.

## Confidence Band Multipliers

| Band        | Multiplier | Notes                                          |
|-------------|------------|------------------------------------------------|
| Optimistic  | 0.6x       | Best case — fast, focused agent work           |
| Expected    | 1.0x       | Typical run                                    |
| Pessimistic | 3.0x       | With rework loops, debugging, re-reads         |

## Context Accumulation

Each step's input tokens grow as prior turns accumulate in the context window.
Approximation: multiply step_input_complex by (K+1)/2, where K = total activity count in the step.

This models triangular growth: first activity sees 1x context, last sees Kx, average is (K+1)/2.
Cache hit rate applies to the repeated prefix portion of accumulated input.

## Partial Pipeline

To estimate only specific steps, use the `steps:` override (e.g., `steps:implement,test,qa`).
The skill sums only the specified steps. All other formula steps remain identical.
