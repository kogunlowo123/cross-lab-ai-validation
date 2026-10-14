A lab AI model that performed well at one hospital is not proven at the next. Patients, analysers and prevalence differ, and the lab director still signs for every result.

**The problem.** Site verification is often a one-off spreadsheet, rarely repeated when the population drifts, and sites cannot pool patient data to compare.

**Why it matters.** CLIA expects each lab to verify accuracy, precision and reportable range before reporting. Agents can do the evidence work, but should never make that call.

**Architecture.** Site → local summariser (aggregates only) → benchmark → shift detection → protocol validation → MCP/A2A agents → policy and identity fabric → medical-director signature → signed audit and metrics.

**What we built.**
1. A registry of model versions, each with its validation protocol and reference population.
2. Aggregate statistics that still give exact sensitivity, specificity, AUROC with a bootstrap interval, calibration and subgroup gaps.
3. Shift checks: PSI, KS, chi-square and label-shift estimation.
4. Method verification: agreement, EP15-style precision, EP6-style linearity.
5. Sign-off by a medical director of that site only, bound to a digest of the evidence.

**Practices.** Agents have no signing tool. Local policy and the identity fabric both decide; the stricter wins. Free text is scanned for identifiers. Every decision lands in a signed hash chain. Outages fail closed.

**Tradeoffs.** Binned scores cap AUROC resolution. Label-shift estimates assume stable class-conditional behaviour. A major shift withdraws approval at once, so false alarms cost lab time. Two authorization hops add latency.

**Value.** Each site's evidence stays current and attributable, and inspectors can rerun every number.

**Next.** As labs adopt more models, continuous site verification may matter as much as the first validation.

Should a major population shift switch a model off automatically, or only alert the lab director?

Code: https://github.com/kogunlowo123/cross-lab-ai-validation

#AI #ArtificialIntelligence #GenerativeAI #AgenticAI #LLMOps #EnterpriseAI #AISecurity #RAG #MachineLearning #DataScience #CloudComputing #Kubernetes #MLOps #DigitalTransformation #Innovation #LaboratoryMedicine #CLIA
