static partial class AecoRevit
{
    public static Transform Frame(Element e)
    {
        if (e is FamilyInstance fi) return fi.GetTransform();
        var frame = Transform.Identity;
        if (e.Location is LocationCurve lc && lc.Curve.IsBound)
        {
            frame.Origin = lc.Curve.GetEndPoint(0);
            var direction = (lc.Curve.GetEndPoint(1) - frame.Origin).Normalize();
            if (e is Pipe)
            {
                frame.BasisZ = direction;
                frame.BasisX = (Math.Abs(direction.DotProduct(XYZ.BasisY)) < 0.99 ? XYZ.BasisY : XYZ.BasisX).CrossProduct(direction).Normalize();
                frame.BasisY = direction.CrossProduct(frame.BasisX).Normalize();
            }
            else
            {
                frame.BasisX = direction;
                frame.BasisY = XYZ.BasisZ.CrossProduct(direction).Normalize();
                frame.BasisZ = direction.CrossProduct(frame.BasisY).Normalize();
            }
        }
        else if (e.Location is LocationPoint lp) frame.Origin = lp.Point;
        return frame;
    }
    public static double[][] Matrix(Transform t) => new[] {
        new[] { t.BasisX.X, t.BasisX.Y, t.BasisX.Z, 0.0 },
        new[] { t.BasisY.X, t.BasisY.Y, t.BasisY.Z, 0.0 },
        new[] { t.BasisZ.X, t.BasisZ.Y, t.BasisZ.Z, 0.0 },
        new[] { Metres(t.Origin.X), Metres(t.Origin.Y), Metres(t.Origin.Z), 1.0 }
    };
    public static Transform FromMatrix(JsonNode m)
    {
        var t = Transform.Identity;
        t.BasisX = new XYZ(N(m[0][0]), N(m[0][1]), N(m[0][2]));
        t.BasisY = new XYZ(N(m[1][0]), N(m[1][1]), N(m[1][2]));
        t.BasisZ = new XYZ(N(m[2][0]), N(m[2][1]), N(m[2][2]));
        t.Origin = Vector(m[3]);
        return t;
    }
    public static Transform InputFrame(Context c, JsonNode edit, Element e)
    {
        if (!c.Bindings.TryGetValue(S(edit["path"]), out var binding) || !(binding is JsonObject) || binding["matrix"] == null)
            throw new Refusal("sync:unbound", "Element-local edits require a binding world matrix");
        return FromMatrix(binding["matrix"]);
    }
    public static PipeSegment Segment(Pipe pipe)
    {
        var id = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_SEGMENT_PARAM)?.AsElementId();
        if (id != null && pipe.Document.GetElement(id) is PipeSegment actual) return actual;
        var manager = pipe.PipeType.RoutingPreferenceManager;
        var segments = new List<PipeSegment>();
        for (int i = 0; i < manager.GetNumberOfRules(RoutingPreferenceRuleGroupType.Segments); i++)
        {
            var rule = manager.GetRule(RoutingPreferenceRuleGroupType.Segments, i);
            if (pipe.Document.GetElement(rule.MEPPartId) is PipeSegment candidate && !segments.Any(s => s.Id == candidate.Id)) segments.Add(candidate);
        }
        if (segments.Count != 1) throw new Refusal("pipeSizeNotInTable", "The active pipe segment size table is missing or ambiguous");
        return segments[0];
    }
    public static Dictionary<string, object> BindingOf(Context c, Element e) => D(
        "ref", e.UniqueId, "localRef", LocalId(e.Id), "version", e.VersionGuid.ToString(), "document", c.DocumentId, "ifcGuid", IfcGuid(e));
    public static object ParameterValue(Parameter p)
    {
        switch (p.StorageType)
        {
            case StorageType.Double: return p.AsDouble();
            case StorageType.Integer: return p.AsInteger();
            case StorageType.ElementId: return LocalId(p.AsElementId());
            case StorageType.String: return p.AsString();
            default: return null;
        }
    }
    public static double LengthParameter(Element e, BuiltInParameter key) => Metres(e.get_Parameter(key)?.AsDouble() ?? 0);
    public static string[] LocationLines = { "centerline", "coreCenterline", "finishFaceExterior", "finishFaceInterior", "coreFaceExterior", "coreFaceInterior" };
    public static Dictionary<string, object> Record(Context c, Element e)
    {
        var row = BindingOf(c, e);
        row["path"] = PathOf(c, e);
        row["tag"] = e.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.AsString() ?? "";
        var frame = Frame(e); var inverse = frame.Inverse;
        row["matrix"] = Matrix(frame);
        var drivers = D(); var derived = D(); var joins = D();
        row["drivers"] = drivers; row["derived"] = derived; row["joins"] = joins;
        row["generated"] = new string[0];
        row["kind"] = e is Pipe ? "pipe" : e is Wall ? "wall" : IsFitting(e) ? "fitting" : "element";
        var type = c.Doc.GetElement(e.GetTypeId());
        if (type != null) row["type"] = BindingOf(c, type);
        if (e.Location is LocationCurve lc && lc.Curve.IsBound)
        {
            drivers["aeco:axis:start"] = Point(inverse.OfPoint(lc.Curve.GetEndPoint(0)));
            drivers["aeco:axis:end"] = Point(inverse.OfPoint(lc.Curve.GetEndPoint(1)));
            drivers["aeco:axis:curve"] = lc.Curve is Arc ? "arc" : "line";
            if (lc.Curve is Arc) drivers["aeco:axis:arcPoint"] = Point(inverse.OfPoint(lc.Curve.Evaluate(0.5, true)));
            derived["aeco:axis:length"] = Metres(lc.Curve.Length);
        }
        if (e is Pipe pipe)
        {
            drivers["aeco:pipe:nominalDiameter"] = Metres(pipe.Diameter);
            var sizes = Segment(pipe).GetSizes().OrderBy(s => s.NominalDiameter).ToList();
            var size = sizes.FirstOrDefault(s => Math.Abs(s.NominalDiameter - pipe.Diameter) < Feet(1e-9));
            if (size != null)
            {
                derived["aeco:pipe:outerDiameter"] = Metres(size.OuterDiameter);
                derived["aeco:pipe:innerDiameter"] = Metres(size.InnerDiameter);
            }
            derived["aeco:pipe:sizeLabel"] = "DN" + Math.Round(Metres(pipe.Diameter) * 1000).ToString(System.Globalization.CultureInfo.InvariantCulture);
            row["sizeTable"] = sizes.Select(s => D("nominal", Metres(s.NominalDiameter), "outer", Metres(s.OuterDiameter), "inner", Metres(s.InnerDiameter))).ToArray();
        }
        if (e is Wall wall)
        {
            drivers["aeco:wall:height"] = LengthParameter(e, BuiltInParameter.WALL_USER_HEIGHT_PARAM);
            drivers["aeco:wall:baseOffset"] = LengthParameter(e, BuiltInParameter.WALL_BASE_OFFSET);
            drivers["aeco:wall:topOffset"] = LengthParameter(e, BuiltInParameter.WALL_TOP_OFFSET);
            drivers["aeco:wall:flipped"] = wall.Flipped;
            int line = e.get_Parameter(BuiltInParameter.WALL_KEY_REF_PARAM).AsInteger();
            drivers["aeco:wall:locationLine"] = LocationLines[line];
            derived["aeco:wall:thickness"] = Metres(wall.Width);
            var wl = (LocationCurve)e.Location;
            for (int end = 0; end < 2; end++)
            {
                string suffix = end == 0 ? "Start" : "End";
                drivers["aeco:wall:allowJoinAt" + suffix] = WallUtils.IsWallJoinAllowedAtEnd(wall, end);
                var joined = wl.get_ElementsAtJoin(end).Cast<Element>().Where(j => j.Id != e.Id).Select(j => j.UniqueId).ToArray();
                joins["aeco:wall:joinAt" + suffix] = joined;
                string join = wl.get_JoinType(end).ToString();
                drivers["aeco:wall:joinType" + suffix] = join == "Miter" ? "miter" : join == "Butt" ? "butt" : join == "SquareOff" ? "squareOff" : "auto";
            }
            row["layers"] = wall.WallType.GetCompoundStructure()?.GetLayers().Select(l => D("width", Metres(l.Width), "function", l.Function.ToString(), "materialId", LocalId(l.MaterialId))).ToArray();
        }
        if (e is FamilyInstance hosted && hosted.Host != null) row["hostRef"] = hosted.Host.UniqueId;
        row["ports"] = (IsCamera(e) ? new List<Connector>() : Ports(e)).Select(p => {
            var peer = Peers(p);
            var result = D("ref", PortRef(p), "localRef", p.Id.ToString(), "connectorId", p.Id,
                "origin", Point(p.Origin), "radius", Metres(p.Radius), "diameter", Metres(p.Radius * 2),
                "matrix", Matrix(inverse.Multiply(p.CoordinateSystem)), "connected", peer.Select(PortRef).ToArray(),
                "refs", peer.Select(other => D("ref", PortRef(other), "ownerRef", other.Owner.UniqueId, "ifcGuid", IfcGuid(other.Owner),
                    "connectorId", other.Id, "origin", Point(other.Origin), "radius", Metres(other.Radius))).ToArray());
            return result;
        }).ToArray();
        row["parameters"] = e.Parameters.Cast<Parameter>().Where(p => p.HasValue).OrderBy(p => p.Id.Value)
            .Select(p => D("id", LocalId(p.Id), "storage", p.StorageType.ToString(), "dataType", p.Definition.GetDataType().TypeId,
                "internalValue", ParameterValue(p), "readOnly", p.IsReadOnly)).ToArray();
        if (IsCamera(e)) CameraRecord(c, (FamilyInstance)e, row);
        return row;
    }
    public static void Triangles(GeometryElement geometry, Transform placement, Transform inverse, List<double> vertices, List<int> faces, Document cameraDoc = null)
    {
        if (geometry == null) return;
        foreach (GeometryObject item in geometry)
        {
            if (cameraDoc != null && CameraGuide(item, cameraDoc)) continue;
            if (item is GeometryInstance instance)
                Triangles(instance.GetSymbolGeometry(), placement.Multiply(instance.Transform), inverse, vertices, faces, cameraDoc);
            else if (item is Solid solid && solid.Faces.Size > 0 && solid.Volume > 0)
            {
                foreach (Face face in solid.Faces)
                {
                    if (cameraDoc != null && CameraGuide(face, cameraDoc)) continue;
                    var mesh = face.Triangulate();
                    for (int i = 0; i < mesh.NumTriangles; i++)
                    {
                        var triangle = mesh.get_Triangle(i);
                        for (int j = 0; j < 3; j++)
                        {
                            faces.Add(vertices.Count / 3);
                            vertices.AddRange(Point(inverse.OfPoint(placement.OfPoint(triangle.get_Vertex(j)))));
                        }
                    }
                }
            }
        }
    }
    public static Dictionary<string, object> MeshOf(Element e)
    {
        var vertices = new List<double>(); var faces = new List<int>();
        Triangles(e.get_Geometry(new Options { DetailLevel = ViewDetailLevel.Fine, IncludeNonVisibleObjects = false }), Transform.Identity, Frame(e).Inverse, vertices, faces, IsCamera(e) ? e.Document : null);
        if (vertices.Count == 0) throw new Refusal("revit:geometry", "Element has no tessellated solid: " + e.UniqueId);
        return D("verts", vertices, "faces", faces);
    }
    public static string Fingerprint(Context c)
    {
        var records = Scope(c.Doc).Select(e => {
            var row = Record(c, e); row.Remove("path"); return row;
        }).ToArray();
        byte[] bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(D("document", c.DocumentId, "elements", records)));
        return Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(bytes)).ToLowerInvariant();
    }
    public static void Receipt(Context c, IEnumerable<Element> elements)
    {
        var records = new List<Dictionary<string, object>>(); var meshes = D();
        foreach (var e in elements.Where(e => e != null && e.IsValidObject).DistinctBy(e => e.Id).OrderBy(e => e.UniqueId))
        {
            // Limit publication, while Fingerprint still covers the full adapter scope.
            if ((c.Request["camerasOnly"]?.GetValue<bool>() ?? false) && !IsCamera(e)) continue;
            var row = Record(c, e);
            if (c.Added.Contains(e.Id) && IsFitting(e))
            {
                row["origin"] = "generated";
                row["generated"] = new[] { e.UniqueId };
                ((Dictionary<string, object>)row["drivers"])["aeco:pipeFitting:origin"] = "generated";
            }
            records.Add(row); meshes[e.UniqueId] = MeshOf(e);
        }
        c.Reply["touched"] = records;
        c.Reply["meshes"] = meshes;
        c.Reply["version"] = Fingerprint(c);
        c.Reply["document"] = c.DocumentId;
        c.Reply["stamp"] = "Revit " + c.Doc.Application.VersionNumber + "/" + c.Doc.Application.VersionBuild + " / usdAecoSync Revit 0.4.1";
        c.Reply["diagnostics"] = c.Diagnostics;
    }
}
