static partial class AecoRevit
{
    public static void ExportIfc(Context c)
    {
        string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "usdaeco-export-" + Guid.NewGuid().ToString("N"));
        System.IO.Directory.CreateDirectory(directory);
        try
        {
            var options = new IFCExportOptions { FileVersion = IFCVersion.IFC4, ExportBaseQuantities = true, WallAndColumnSplitting = false };
            options.AddOption("ExportIFCCommonPropertySets", "true");
            options.AddOption("ExportInternalRevitPropertySets", "false");
            options.AddOption("ExportLinkedFiles", "false");
            options.AddOption("StoreIFCGUID", "true");
            using (var tx = Tx(c, "usdAeco IFC4 snapshot"))
            {
                tx.Start();
                if (!c.Doc.Export(directory, "snapshot", options)) throw new Refusal("revit:export", "Document.Export returned false");
                if (tx.Commit() != TransactionStatus.Committed) throw new Refusal("revit:export", "IFC export transaction rolled back");
            }
            byte[] bytes = System.IO.File.ReadAllBytes(System.IO.Path.Combine(directory, "snapshot.ifc"));
            c.Reply["ifc"] = D("base64", Convert.ToBase64String(bytes), "bytes", bytes.Length,
                "sha256", Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(bytes)).ToLowerInvariant(), "schema", "IFC4");
        }
        finally { System.IO.Directory.Delete(directory, true); }
    }
    public static Dictionary<string, object> Run(UIApplication app, string json)
    {
        Context c = null;
        try
        {
            c = new Context(app, JsonNode.Parse(json).AsObject());
            var edits = c.Request["edits"]?.AsArray() ?? new JsonArray();
            string action = S(c.Request["action"]);
            if (action != "sync" && action != "snapshot") throw new Refusal("sync:unsupported", "Unknown request action");
            if (action == "snapshot" && edits.Count != 0) throw new Refusal("sync:unsupported", "Snapshots cannot contain edits");
            if (!new[] { "keepConnected", "disconnect", "refuse" }.Contains(S(c.Request["policy"])))
                throw new Refusal("sync:unsupported", "Unknown gap policy");
            string beforeVersion = Fingerprint(c);
            string expectedVersion = S(c.Request["expectedVersion"]);
            if (action == "sync" && (expectedVersion == "" || expectedVersion != beforeVersion))
                throw new Refusal("sync:staleIntent", "Native state changed; read back before applying intent");
            CheckEdits(c, edits);
            if (c.Request["camerasOnly"]?.GetValue<bool>() ?? false)
            {
                foreach (var edit in edits)
                    if (S(edit["operation"]) == "create" ? S(edit["value"]?["kind"]) != "camera" : !IsCamera(Lookup(c, S(edit["ref"]))))
                        throw new Refusal("sync:unsupported", "Camera scope accepts camera edits only");
            }
            bool export = c.Request["exportIfc"]?.GetValue<bool>() ?? false;
            bool restore = c.Request["rollbackOnly"]?.GetValue<bool>() ?? false;
            var before = Scope(c.Doc).ToDictionary(e => e.Id, e => e.UniqueId);
            var beforeRecords = Scope(c.Doc).ToDictionary(e => e.UniqueId, e => JsonSerializer.Serialize(Record(c, e)));
            // Resolve existing owners before deletion. Creations have no native ref yet.
            var editedRefs = edits.Where(e => S(e["operation"]) != "create")
                .Select(e => Lookup(c, S(e["ref"])).UniqueId).ToHashSet();
            if (action == "snapshot" && !export && !restore)
            {
                Receipt(c, Scope(c.Doc)); Warnings(c); c.Reply["status"] = "snapshot";
                c.Reply["rollbackVerified"] = true;
                c.Reply["wallTypes"] = new FilteredElementCollector(c.Doc).OfClass(typeof(WallType)).Cast<WallType>()
                    .Where(t => t.Kind == WallKind.Basic).Select(t => { var row = BindingOf(c, t); row["width"] = Metres(t.Width); return row; }).ToArray();
                return c.Reply;
            }
            using (var group = new TransactionGroup(c.Doc, "usdAeco sync"))
            {
                group.Start();
                EventHandler<Autodesk.Revit.DB.Events.DocumentChangedEventArgs> changed = (sender, args) => {
                    if (args.GetDocument() != c.Doc) return;
                    c.Changed.UnionWith(args.GetModifiedElementIds());
                    c.Changed.UnionWith(args.GetDeletedElementIds());
                    c.Added.UnionWith(args.GetAddedElementIds());
                };
                app.Application.DocumentChanged += changed;
                try
                {
                    if (edits.Count > 0)
                    {
                        using (var tx = Tx(c, "usdAeco apply drivers"))
                        {
                            tx.Start();
                            foreach (var port in c.Request["closure"]?["disconnect"]?.AsArray() ?? new JsonArray()) Disconnect(c, ConnectorAt(c, S(port)));
                            // Remove connections before any geometric edit, independent of wire order.
                            foreach (var edit in edits.OrderBy(e => RemovesJoin(e) || S(e["name"]) == "aeco:connectedPorts" && e["value"].AsArray().Count == 0 ? 0
                                : S(e["name"]) == "inheritPaths" && IsCamera(Lookup(c, S(e["ref"]))) ? 1 : 2)) ApplyEdit(c, edit);
                            foreach (var camera in Scope(c.Doc).Where(e => IsCamera(e) && c.Changed.Contains(e.Id)).Cast<FamilyInstance>())
                                CheckCameraFinalFocals(camera);
                            if (tx.Commit() != TransactionStatus.Committed) throw new Refusal("revit:transactionRolledBack", "Failure processing rolled back the native transaction");
                        }
                    }
                    var after = Scope(c.Doc);
                    c.Added.UnionWith(after.Where(e => !before.ContainsKey(e.Id)).Select(e => e.Id));
                    var generated = after.Where(e => c.Added.Contains(e.Id) && IsFitting(e)).ToList();
                    if (generated.Count > 0)
                    {
                        using (var tx = Tx(c, "usdAeco generated fitting identities"))
                        {
                            tx.Start();
                            foreach (var e in generated)
                            {
                                var parameter = e.get_Parameter(BuiltInParameter.IFC_GUID);
                                if (parameter == null || parameter.IsReadOnly || !parameter.Set(IfcGuid(e)))
                                    throw new Refusal("revit:identityPersistence", "Generated fitting IFC_GUID cannot be persisted");
                            }
                            if (tx.Commit() != TransactionStatus.Committed) throw new Refusal("revit:transactionRolledBack", "Generated identity transaction rolled back");
                        }
                    }
                    if (export) ExportIfc(c);
                    after = Scope(c.Doc);
                    c.Changed.UnionWith(after.Where(e => !beforeRecords.TryGetValue(e.UniqueId, out var old) || old != JsonSerializer.Serialize(Record(c, e))).Select(e => e.Id));
                    // Read the full requested topology closure plus every commit-time side effect.
                    foreach (var path in c.Request["closure"]?["paths"]?.AsArray() ?? new JsonArray())
                    {
                        // A deleted camera (and its sub-instances) has no lookup.
                        if (edits.Any(e => S(e["path"]) == S(path) && S(e["operation"]) == "activation" && !e["value"].GetValue<bool>())) continue;
                        c.Changed.Add(Lookup(c, S(path)).Id);
                    }
                    // Facility rollback cases publish changed cameras only;
                    // full convergence uses a rollback snapshot explicitly.
                    var touched = action == "snapshot" || export || restore && !(c.Request["camerasOnly"]?.GetValue<bool>() ?? false)
                        ? after : after.Where(e => c.Changed.Contains(e.Id) || c.Added.Contains(e.Id)).ToList();
                    CheckGaps(c, touched);
                    Receipt(c, touched);
                    var records = (List<Dictionary<string, object>>)c.Reply["touched"];
                    foreach (var deleted in before.Where(p => c.Doc.GetElement(p.Key) == null))
                        records.Add(D("ref", deleted.Value, "path", c.Paths.TryGetValue(deleted.Value, out var path) ? path : "", "active", false));
                    Warnings(c);
                    var neighbours = after.Where(e => c.Changed.Contains(e.Id) && !editedRefs.Contains(e.UniqueId) && !c.Added.Contains(e.Id)).Select(e => e.UniqueId).ToArray();
                    if (neighbours.Length > 0) c.Diagnostics.Add(Diagnostic("info", "sync:neighbourMoved", "Revit regenerated connected or hosted neighbours", "apply", false, neighbours));
                    c.Reply["status"] = "committed";
                    if (restore)
                    {
                        group.RollBack();
                        c.Reply["restoredVersion"] = Fingerprint(c);
                        c.Reply["rollbackVerified"] = (string)c.Reply["restoredVersion"] == beforeVersion;
                        c.Reply["rollbackOnly"] = true;
                        if (!(bool)c.Reply["rollbackVerified"]) throw new Refusal("revit:rollbackMismatch", "Scenario rollback did not restore its state fingerprint");
                    }
                    else if (group.Assimilate() != TransactionStatus.Committed)
                        throw new Refusal("revit:transactionRolledBack", "Sync group did not commit");
                }
                catch
                {
                    if (group.GetStatus() == TransactionStatus.Started) group.RollBack();
                    throw;
                }
                finally { app.Application.DocumentChanged -= changed; }
            }
            return c.Reply;
        }
        catch (Exception error)
        {
            var diagnostic = Diagnostic("error", error is Refusal refusal ? refusal.Code : "revit:exception:" + error.GetType().Name, error.Message, "apply", true);
            if (c == null) return D("touched", new object[0], "meshes", D(), "diagnostics", new[] { diagnostic }, "version", "", "document", "", "status", "refused", "stamp", "usdAecoSync Revit 0.4.1");
            c.Diagnostics.Add(diagnostic);
            // Failed results describe the restored document, never tentative geometry.
            c.Reply.Remove("ifc");
            c.Reply["touched"] = new object[0]; c.Reply["meshes"] = D();
            c.Reply["diagnostics"] = c.Diagnostics; c.Reply["status"] = "refused";
            c.Reply["document"] = c.DocumentId; c.Reply["stamp"] = "usdAecoSync Revit 0.4.1";
            try { c.Reply["version"] = Fingerprint(c); }
            catch { c.Reply["version"] = ""; }
            c.Reply["rollbackVerified"] = S(c.Request["expectedVersion"]) == (string)c.Reply["version"];
            foreach (var d in c.Diagnostics) d["rolledBack"] = true;
            try { Warnings(c); }
            catch (Exception warningError) { c.Diagnostics.Add(Diagnostic("error", "revit:warningReadback", warningError.Message, "validate", true)); }
            return c.Reply;
        }
    }
    public static string Transport(UIApplication app, string json, string key)
    {
        byte[] reply = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(Run(app, json)));
        string encoding = "json";
        if (JsonNode.Parse(json)["compactReply"]?.GetValue<bool>() ?? false)
        {
            using (var output = new System.IO.MemoryStream())
            {
                using (var gzip = new System.IO.Compression.GZipStream(output, System.IO.Compression.CompressionLevel.Optimal, true))
                    gzip.Write(reply, 0, reply.Length);
                reply = output.ToArray();
            }
            encoding = "gzip";
        }
        AppDomain.CurrentDomain.SetData(key, reply);
        return JsonSerializer.Serialize(D("key", key, "bytes", reply.Length, "encoding", encoding, "sha256", Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(reply)).ToLowerInvariant()));
    }
}
