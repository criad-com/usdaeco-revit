// Script pack for the existing Revit REPL. Loaded together for each request.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Plumbing;
using Autodesk.Revit.UI;

static partial class AecoRevit
{
    public sealed class Refusal : Exception
    {
        public string Code;
        public Refusal(string code, string message) : base(message) { Code = code; }
    }

    public sealed class Context
    {
        public Document Doc;
        public JsonObject Request;
        public Dictionary<string, object> Reply = D();
        public List<Dictionary<string, object>> Diagnostics = new List<Dictionary<string, object>>();
        public Dictionary<string, string> Paths = new Dictionary<string, string>();
        public Dictionary<string, JsonNode> Bindings = new Dictionary<string, JsonNode>();
        public HashSet<ElementId> Changed = new HashSet<ElementId>();
        public HashSet<ElementId> Added = new HashSet<ElementId>();
        public string DocumentId;
        public Context(UIApplication app, JsonObject request)
        {
            Request = request;
            Doc = app.ActiveUIDocument?.Document;
            string expectedPath = S(request["expectedPath"]);
            if (request["backgroundDocument"]?.GetValue<bool>() ?? false)
            {
                var foreground = Doc;
                Doc = app.Application.Documents.Cast<Document>().SingleOrDefault(d => d != foreground
                    && String.Equals(d.PathName, expectedPath, StringComparison.OrdinalIgnoreCase));
            }
            if (Doc == null || Doc.IsFamilyDocument || Doc.IsReadOnly || Doc.IsModifiable)
                throw new Refusal("revit:documentGuard", "A writable resident project in the requested document mode with no active transaction is required");
            if (String.IsNullOrWhiteSpace(expectedPath) || !String.Equals(Doc.PathName, expectedPath, StringComparison.OrdinalIgnoreCase))
                throw new Refusal("revit:documentGuard", "Resident document path does not match the request");
            // A durable project export GUID, independent of path, local ElementId or save count.
            DocumentId = ExportUtils.GetExportId(Doc, Doc.ProjectInformation.Id).ToString();
            string expectedDocument = S(request["document"]);
            if (expectedDocument != "" && expectedDocument != DocumentId)
                throw new Refusal("revit:documentGuard", "Requested project GUID does not match the binding");
            foreach (var pair in request["bindings"]?.AsObject() ?? new JsonObject())
            {
                Bindings[pair.Key] = pair.Value;
                string reference = pair.Value is JsonObject ? S(pair.Value["ref"]) : S(pair.Value);
                if (reference != "") Paths[reference] = pair.Key;
                if (pair.Value is JsonObject)
                {
                    string guid = S(pair.Value["ifcGuid"]);
                    if (guid != "") Paths[guid] = pair.Key;
                    string document = S(pair.Value["document"]);
                    if (document != "" && document != DocumentId)
                        throw new Refusal("revit:documentGuard", "A binding belongs to another project");
                }
            }
        }
    }

    public static Dictionary<string, object> D(params object[] pairs)
    {
        var result = new Dictionary<string, object>();
        for (int i = 0; i < pairs.Length; i += 2) result[(string)pairs[i]] = pairs[i + 1];
        return result;
    }
    public static string S(JsonNode node) => node == null ? "" : node.GetValue<string>();
    public static double N(JsonNode node) => node.GetValue<double>();
    public static double Metres(double feet) => UnitUtils.ConvertFromInternalUnits(feet, UnitTypeId.Meters);
    public static double Feet(double metres) => UnitUtils.ConvertToInternalUnits(metres, UnitTypeId.Meters);
    public static double[] Point(XYZ p) => new[] { Metres(p.X), Metres(p.Y), Metres(p.Z) };
    public static XYZ Vector(JsonNode p) => new XYZ(Feet(N(p[0])), Feet(N(p[1])), Feet(N(p[2])));
    public static string LocalId(ElementId id) => id.Value.ToString(System.Globalization.CultureInfo.InvariantCulture);
    public static ConnectorManager Manager(Element e) => (e as MEPCurve)?.ConnectorManager ?? (e as FamilyInstance)?.MEPModel?.ConnectorManager;
    public static List<Connector> Ports(Element e) => Manager(e)?.Connectors.Cast<Connector>().Where(c => c.ConnectorType == ConnectorType.End).OrderBy(c => c.Id).ToList() ?? new List<Connector>();
    public static List<Connector> Peers(Connector c) => c.AllRefs.Cast<Connector>().Where(p => p.Owner.Id != c.Owner.Id && p.ConnectorType == ConnectorType.End).OrderBy(p => p.Owner.UniqueId).ThenBy(p => p.Id).ToList();
    public static string PortRef(Connector c) => c.Owner.UniqueId + ":" + c.Id;
    public static bool IsFitting(Element e) => e is FamilyInstance && e.Category?.Id.Value == (long)BuiltInCategory.OST_PipeFitting;
    public static List<Element> Scope(Document doc) => new FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements()
        .Where(e => e is Pipe || e is Wall || IsFitting(e) || IsCamera(e) || (e is FamilyInstance fi && fi.Host is Wall)).OrderBy(e => e.UniqueId).ToList();

    public static string IfcCompress(Guid guid)
    {
        byte[] b = guid.ToByteArray();
        long[] values = { b[3], b[2]*65536L+b[1]*256L+b[0], b[5]*65536L+b[4]*256L+b[7], b[6]*65536L+b[8]*256L+b[9], b[10]*65536L+b[11]*256L+b[12], b[13]*65536L+b[14]*256L+b[15] };
        const string alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$";
        string output = "";
        for (int i = 0; i < 6; i++)
        {
            string part = ""; long value = values[i];
            for (int j = 0; j < (i == 0 ? 2 : 4); j++) { part = alphabet[(int)(value % 64)] + part; value /= 64; }
            output += part;
        }
        return output;
    }
    public static string IfcGuid(Element e) => e.get_Parameter(BuiltInParameter.IFC_GUID)?.AsString() is string s && !String.IsNullOrEmpty(s) ? s : IfcCompress(ExportUtils.GetExportId(e.Document, e.Id));
    public static Element Lookup(Context c, string reference)
    {
        if (c.Bindings.ContainsKey(reference))
        {
            var binding = c.Bindings[reference];
            reference = binding is JsonObject ? S(binding["ref"]) : S(binding);
        }
        // Connector refs resolve their owner; ElementId is only a read-back cache.
        if (reference.Contains(":")) reference = reference.Substring(0, reference.IndexOf(':'));
        Element e = null;
        try { e = c.Doc.GetElement(reference); } catch (Autodesk.Revit.Exceptions.ArgumentException) { }
        if (e == null)
        {
            var matches = new FilteredElementCollector(c.Doc).WherePasses(new LogicalOrFilter(new ElementIsElementTypeFilter(), new ElementIsElementTypeFilter(true))).Where(x => IfcGuid(x) == reference).Take(2).ToList();
            if (matches.Count > 1) throw new Refusal("sync:unbound", "Ambiguous IFC GUID binding");
            e = matches.FirstOrDefault();
        }
        if (e == null) throw new Refusal("sync:unbound", "No element for binding " + reference);
        return e;
    }
    public static Connector ConnectorAt(Context c, string pathOrRef)
    {
        string reference = pathOrRef;
        if (c.Bindings.ContainsKey(reference))
        {
            var b = c.Bindings[reference]; reference = b is JsonObject ? S(b["ref"]) : S(b);
        }
        int split = reference.LastIndexOf(':');
        if (split < 0 || !Int32.TryParse(reference.Substring(split + 1), out int id))
            throw new Refusal("sync:unbound", "A port binding must be OwnerUniqueId:ConnectorId");
        var owner = Lookup(c, reference.Substring(0, split));
        return Ports(owner).FirstOrDefault(p => p.Id == id) ?? throw new Refusal("sync:unbound", "Connector no longer exists");
    }
    public static string PathOf(Context c, Element e) => c.Paths.TryGetValue(e.UniqueId, out var p) || c.Paths.TryGetValue(IfcGuid(e), out p) ? p : "";
    public static Dictionary<string, object> Diagnostic(string severity, string code, string message, string phase = "apply", bool blocking = false, IEnumerable<string> refs = null)
        => D("severity", severity, "code", code, "message", message, "phase", phase, "blocking", blocking, "hostRefs", (refs ?? new string[0]).ToArray(), "about", new string[0]);
}
