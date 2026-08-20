## Matthew++

### Overview

We're building a natural-language interface to CAIDA's measurement infrastructure — for the hackathon, scoped specifically to the Ark system, as the first building block toward a larger vision for CAIDA as a whole.

The core idea is *not* "ask an LLM to write measurement code on the fly." Freeform code generation against a system like Ark is exactly where hallucination and messy, one-off scripts creep in. Instead, we're writing a deterministic, well-specified Ark codebase designed to be read and driven by an AI — code *for* AI, rather than code *from* AI. The model's job becomes narrower and safer: convert a natural-language intent into a call against this fixed, deterministic interface (via MCP + RAG), rather than improvising the implementation itself. For example, "take the nodes in Africa and ping google.com for 10 seconds" becomes a concrete, bounded Ark experiment — not generated Python.

### Why this matters: a worked example

A real prior incident illustrates the gap we're trying to close. Traceroutes to a destination were unexpectedly routing through CAIDA's San Diego site before continuing on to the actual destination. Diagnosing that took an expert (Matthew) running several more traceroutes from comparable regions, pulling BGP routing tables for the relevant ASes, and manually correlating all of it to find the actual cause.

That's the pattern we want to automate: a single vantage point, or a single tool, rarely answers a question like this — it takes several traceroutes from multiple regions, cross-referenced against BGP state and other datasets, synthesized into one diagnosis.

### The bigger goal

Beyond the hackathon, we want AI to have controlled, deterministic access to CAIDA's full range of measurement tools and datasets — not just Ark — so it can assemble its own multi-vantage-point view and answer higher-level diagnostic questions the way a human expert would, without needing that expert to run every step by hand.

LLMs are already good at generating hypotheses about root causes — the "educated guess" part is largely solved. What's missing is the ability to safely test those hypotheses: to actually run the traceroute, check the BGP table, pull the dataset, and see if the guess holds up. Give the model a properly scoped, deterministic interface to do that, and it can validate its own ideas without expert intervention at every step.

Not everyone has a Matthew on call to get them the answer. Matthew++ is about making that kind of expertise available to anyone who can describe what they want to know — so people can spend their time on the idea, not the implementation.
