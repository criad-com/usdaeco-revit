static partial class AecoRevit
{
    public sealed class FailRec : IFailuresPreprocessor
    {
        public readonly Context Context;
        public FailRec(Context context) { Context = context; }
        public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
        {
            bool errors = false;
            try
            {
              foreach (var failure in accessor.GetFailureMessages())
              {
                var severity = failure.GetSeverity();
                bool error = severity == FailureSeverity.Error || severity == FailureSeverity.DocumentCorruption;
                errors |= error;
                var row = Diagnostic(error ? "error" : "warning", "revit:" + failure.GetFailureDefinitionId().Guid,
                    failure.GetDescriptionText(), "commit", error, failure.GetFailingElementIds().Select(LocalId));
                row["definitionId"] = failure.GetFailureDefinitionId().Guid.ToString();
                row["nativeSeverity"] = severity.ToString();
                row["failingIds"] = failure.GetFailingElementIds().Select(LocalId).ToArray();
                row["additionalIds"] = failure.GetAdditionalElementIds().Select(LocalId).ToArray();
                row["resolutionCount"] = failure.GetNumberOfResolutions();
                row["resolutions"] = Enum.GetValues(typeof(FailureResolutionType)).Cast<FailureResolutionType>()
                    .Where(r => HasResolution(failure, r)).Select(r => r.ToString()).ToArray();
                if (failure.HasResolutions()) row["defaultResolution"] = failure.GetDefaultResolutionCaption();
                Context.Diagnostics.Add(row);
                // Capture then delete transient warnings so no modal failure UI remains.
                if (severity == FailureSeverity.Warning) accessor.DeleteWarning(failure);
              }
            }
            catch (Exception error)
            {
                // Failure capture must never escape to Revit's modal processor.
                errors = true;
                Context.Diagnostics.Add(Diagnostic("error", "revit:failureCapture", error.Message, "commit", true));
            }
            return errors ? FailureProcessingResult.ProceedWithRollBack : FailureProcessingResult.Continue;
        }
        private static bool HasResolution(FailureMessageAccessor failure, FailureResolutionType type)
        {
            try { return failure.HasResolutionOfType(type); }
            catch (Autodesk.Revit.Exceptions.ArgumentException) { return false; }
        }
    }
    public static Transaction Tx(Context c, string name)
    {
        var tx = new Transaction(c.Doc, name);
        var options = tx.GetFailureHandlingOptions();
        options.SetFailuresPreprocessor(new FailRec(c));
        options.SetClearAfterRollback(true);
        options.SetForcedModalHandling(false);
        tx.SetFailureHandlingOptions(options);
        return tx;
    }
    public static void Warnings(Context c)
    {
        foreach (var warning in c.Doc.GetWarnings())
        {
            var row = Diagnostic("warning", "revit:" + warning.GetFailureDefinitionId().Guid,
                warning.GetDescriptionText(), "validate", false, warning.GetFailingElements().Select(LocalId));
            row["definitionId"] = warning.GetFailureDefinitionId().Guid.ToString();
            row["nativeSeverity"] = warning.GetSeverity().ToString();
            row["failingIds"] = warning.GetFailingElements().Select(LocalId).ToArray();
            row["additionalIds"] = warning.GetAdditionalElements().Select(LocalId).ToArray();
            row["snapshot"] = "Document.GetWarnings";
            c.Diagnostics.Add(row);
        }
    }
}
