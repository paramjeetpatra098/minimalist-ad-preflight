# Product Decision Note

I designed this as a pre-flight tool for Minimalist’s performance marketing team, where the costliest failure is not a mediocre creative but an ad that publishes an unsupported claim, misrepresents the product, or creates unnecessary brand/legal risk.

**The core principle I used was: claim strength should not exceed evidence strength.**

For generation, I did not want the model to freely consume everything found on a product page and rely on the scorer to catch mistakes later. Product information is first classified based on whether it is safe to use. Source-backed facts can be used directly; some claims can be used only if their conditions or qualifiers are preserved; unsupported or unassessable information is excluded. The marketer can inspect this as “Evidence available for generation,” but the underlying control happens before generation.

The generated creative then goes through a separate pre-flight review across three dimensions: **Policy & Claims, Brand Tone, and Brand Language**. I deliberately avoided a numeric compliance score because a number such as 82/100 can hide a serious unsupported claim. The output instead uses PASS, PASS with warnings, REVIEW, or BLOCK, with the exact issue, rule, reason, evidence and suggested action.

I made unresolved **REVIEW and BLOCK non-exportable**. Earlier, I considered allowing a marketer to acknowledge a REVIEW and continue. I changed this after thinking through what acknowledgement actually proves: nothing. A REVIEW now has to be resolved by adding sufficient evidence, removing or weakening the claim, or replacing it with supported wording. Human escalation alone does not turn uncertainty into compliance.

The correction flow follows the same principle. AI can suggest a narrow revision, but it cannot create missing evidence. Every accepted change triggers the full scorer again before export. Testing also exposed an important edge case where sequential fixes could remove all ad copy and produce a technically clean PASS. I changed the product so an empty or meaningless creative cannot pass simply because there is nothing left to violate.

For external creatives, product context is optional because marketers may want to review work created elsewhere. When evidence or an image is missing, the system makes that limitation explicit rather than assuming compliance. Copy-only review is supported, but it is not presented as a complete creative review and does not offer creative export.

I intentionally kept V1 narrow: one static 1080×1080 Meta creative, India context, one product at a time, no direct publishing, no approval workflow, no user accounts, no persistent revision history, no analytics, and no AI-generated product packaging. Actual product imagery is used because recreating packaging introduces unnecessary factual and brand risk.

The product therefore optimizes for **traceable, evidence-bounded decisions over automation breadth**. A PASS means the creative passed the checks encoded in this tool using the evidence available to it. It does not mean legal approval, Meta approval, or a substitute for Minimalist’s internal brand/legal process.
