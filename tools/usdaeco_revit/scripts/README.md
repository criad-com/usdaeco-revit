# Native script enclave

These C# scripts are a declared non-Nix enclave. Revit compiles them in its
own REPL process; Python and Nix cannot prove native compilation or API behaviour.
The pack is checked against manifest.json before submission. Every logical reply
is paged once and SHA-256 verified. A failed mutation is never replayed.

The document must already be open. Background-document mode locates the exact
resident document by expectedPath and does not open, activate or close documents.
IFC_GUID supplies cross-route identity; Mark is a human-readable label.

Files are re-homed from the sync v0.4.5 pack without C# logic changes; .csx
makes their script role explicit. Live validation in this release is NOT RUN.
