static partial class AecoRevit
{
    public static void Disconnect(Context c, Connector port)
    {
        foreach (var peer in Peers(port))
        {
            c.Changed.Add(peer.Owner.Id);
            port.DisconnectFrom(peer);
        }
        c.Changed.Add(port.Owner.Id);
    }
    public static bool RemovesJoin(JsonNode edit) =>
        (S(edit["operation"]) == "relationship" && S(edit["name"]).StartsWith("aeco:wall:joinAt") && edit["value"].AsArray().Count == 0)
        || (S(edit["name"]).StartsWith("aeco:wall:allowJoinAt") && !edit["value"].GetValue<bool>());
    public static void CheckEdits(Context c, JsonArray edits)
    {
        foreach (var edit in edits)
        {
            if (S(edit["operation"]) == "create")
            {
                if (S(edit["value"]?["kind"]) != "camera") throw new Refusal("sync:unsupported", "Only camera creation is supported here");
                continue;
            }
            var e = Lookup(c, S(edit["ref"]));
            string name = S(edit["name"]); string operation = S(edit["operation"]);
            if (c.Bindings.TryGetValue(S(edit["path"]), out var b) && b is JsonObject && S(b["version"]) != "" && S(b["version"]) != e.VersionGuid.ToString())
                throw new Refusal("sync:staleIntent", "Element VersionGuid changed");
            if (IsCamera(e)) { CheckCameraEdit(c, (FamilyInstance)e, edit); continue; }
            bool allowed =
                operation == "attribute" && (
                    (e is Pipe || e is Wall) && (name == "aeco:axis:start" || name == "aeco:axis:end")
                    || name == "xformOp:transform" && (e is Pipe || e is Wall || IsFitting(e))
                    || e is Pipe && name == "aeco:pipe:nominalDiameter"
                    || e is Wall && new[] { "aeco:wall:flipped", "aeco:wall:height", "aeco:wall:baseOffset", "aeco:wall:topOffset", "aeco:wall:locationLine", "aeco:wall:allowJoinAtStart", "aeco:wall:allowJoinAtEnd" }.Contains(name))
                || operation == "relationship" && (name == "aeco:connectedPorts" || e is Wall && (name == "aeco:wall:joinAtStart" || name == "aeco:wall:joinAtEnd"))
                || operation == "primMetadata" && name == "inheritPaths" && e is Wall;
            if (!allowed) throw new Refusal("sync:unsupported", "Unsupported native operation: " + operation + " " + name);
            if (name == "aeco:pipe:nominalDiameter")
            {
                double diameter = Feet(N(edit["value"]));
                if (!Segment((Pipe)e).GetSizes().Any(s => Math.Abs(s.NominalDiameter - diameter) < Feet(1e-9)))
                    throw new Refusal("pipeSizeNotInTable", "Nominal diameter is absent from the active PipeSegment table");
            }
            if (name == "aeco:axis:start" || name == "aeco:axis:end")
            {
                if (!(e.Location is LocationCurve lc) || !(lc.Curve is Line))
                    throw new Refusal("sync:unsupported", "Only straight native curve authoring is supported");
                int end = name.EndsWith(":end") ? 1 : 0;
                if (e is Wall wall)
                {
                    bool joined = lc.get_ElementsAtJoin(end).Cast<Element>().Any(other => other.Id != e.Id);
                    string suffix = end == 0 ? "Start" : "End";
                    bool removal = edits.Any(other => S(other["ref"]) == S(edit["ref"]) && S(other["name"]).EndsWith(suffix) && RemovesJoin(other));
                    if (joined && !removal)
                        throw new Refusal("sync:joinedEnd", "Curve edit moves a joined wall end; disallow the join in the same request");
                }
            }
        }
    }
    public static void ApplyEdit(Context c, JsonNode edit)
    {
        if (S(edit["operation"]) == "create") { CreateCamera(c, edit); return; }
        var e = Lookup(c, S(edit["ref"]));
        string name = S(edit["name"]); string operation = S(edit["operation"]);
        c.Changed.Add(e.Id);
        if (IsCamera(e)) { ApplyCameraEdit(c, (FamilyInstance)e, edit); c.Doc.Regenerate(); return; }
        if (operation == "primMetadata" && name == "inheritPaths")
        {
            var paths = edit["value"].AsArray();
            if (paths.Count != 1 || !(Lookup(c, S(paths[0])) is WallType type))
                throw new Refusal("sync:unsupported", "Wall type swap requires one bound WallType catalog class");
            ((Wall)e).WallType = type;
        }
        else if (operation == "relationship" && name == "aeco:connectedPorts")
        {
            var port = ConnectorAt(c, S(edit["path"]));
            var desired = edit["value"].AsArray().Select(target => ConnectorAt(c, S(target))).ToList();
            if (desired.Count > 1) throw new Refusal("sync:unsupported", "One physical peer per port is required; use a fitting for a junction");
            foreach (var peer in Peers(port))
                if (!desired.Any(p => PortRef(p) == PortRef(peer))) { port.DisconnectFrom(peer); c.Changed.Add(peer.Owner.Id); }
            foreach (var peer in desired)
            {
                if (Peers(port).Any(p => PortRef(p) == PortRef(peer))) continue;
                if (peer.IsConnected) throw new Refusal("sync:constrainedEnd", "Join target is already connected");
                string fitting = S(edit["fitting"]);
                FamilyInstance added = null;
                switch (fitting)
                {
                    case "elbow": added = c.Doc.Create.NewElbowFitting(port, peer); break;
                    case "union": added = c.Doc.Create.NewUnionFitting(port, peer); break;
                    case "transition": added = c.Doc.Create.NewTransitionFitting(port, peer); break;
                    case "tee":
                        var third = ConnectorAt(c, S(edit["thirdPort"]));
                        if (third.IsConnected) throw new Refusal("sync:constrainedEnd", "Tee branch is already connected");
                        added = c.Doc.Create.NewTeeFitting(port, peer, third); c.Changed.Add(third.Owner.Id); break;
                    case "":
                    case "direct":
                        if (port.Origin.DistanceTo(peer.Origin) > Feet(1e-4) || Math.Abs(port.Radius - peer.Radius) > Feet(1e-6))
                            throw new Refusal("sync:gap", "Direct ConnectTo requires coincident equally sized ports");
                        port.ConnectTo(peer); break;
                    default: throw new Refusal("sync:unsupported", "Unknown explicit fitting operation");
                }
                if (added != null) { c.Added.Add(added.Id); c.Changed.Add(added.Id); }
                c.Changed.Add(peer.Owner.Id);
            }
        }
        else if (e is Wall joinWall && (name.StartsWith("aeco:wall:allowJoinAt") || operation == "relationship" && name.StartsWith("aeco:wall:joinAt")))
        {
            int end = name.EndsWith("Start") ? 0 : 1;
            bool allow = operation == "relationship" ? edit["value"].AsArray().Count > 0 : edit["value"].GetValue<bool>();
            if (allow) WallUtils.AllowWallJoinAtEnd(joinWall, end); else WallUtils.DisallowWallJoinAtEnd(joinWall, end);
            c.Doc.Regenerate();
            if (operation == "relationship" && allow)
            {
                var expected = edit["value"].AsArray().Select(p => Lookup(c, S(p)).UniqueId).OrderBy(s => s).ToArray();
                var actual = ((LocationCurve)joinWall.Location).get_ElementsAtJoin(end).Cast<Element>().Where(x => x.Id != e.Id).Select(x => x.UniqueId).OrderBy(s => s).ToArray();
                if (!expected.SequenceEqual(actual)) throw new Refusal("sync:joinedEnd", "Allowing this wall end did not produce the requested join set");
            }
        }
        else if (name == "aeco:axis:start" || name == "aeco:axis:end")
        {
            var location = (LocationCurve)e.Location;
            int end = name.EndsWith(":end") ? 1 : 0;
            XYZ target = InputFrame(c, edit, e).OfPoint(Vector(edit["value"]));
            XYZ original = location.Curve.GetEndPoint(end);
            if (target.DistanceTo(location.Curve.GetEndPoint(1 - end)) < Feet(1e-6))
                throw new Refusal("sync:axisDegenerate", "Axis would have zero length");
            Connector port = e is Pipe ? Ports(e).OrderBy(p => p.Origin.DistanceTo(original)).FirstOrDefault() : null;
            var peers = port == null ? new List<Connector>() : Peers(port);
            if (peers.Count > 0)
            {
                string policy = S(c.Request["policy"]);
                if (policy == "refuse") throw new Refusal("sync:constrainedEnd", "Connected end refused by gap policy");
                if (policy == "disconnect") { Disconnect(c, port); peers.Clear(); }
                else
                {
                    if (peers.Count != 1 || !IsFitting(peers[0].Owner))
                        throw new Refusal("sync:constrainedEnd", "Keeping this end connected requires a single fitting at that end");
                    // Critical spike rule: never assign a curve to move a connected end.
                    ElementTransformUtils.MoveElement(c.Doc, peers[0].Owner.Id, target - original);
                    c.Changed.Add(peers[0].Owner.Id);
                }
            }
            if (peers.Count == 0)
            {
                XYZ a = end == 0 ? target : location.Curve.GetEndPoint(0);
                XYZ b = end == 1 ? target : location.Curve.GetEndPoint(1);
                location.Curve = Line.CreateBound(a, b);
            }
        }
        else if (name == "xformOp:transform")
        {
            var binding = c.Bindings[S(edit["path"])];
            var before = InputFrame(c, edit, e);
            var after = FromMatrix(binding["parentMatrix"]).Multiply(FromMatrix(edit["value"]));
            if (!before.BasisX.IsAlmostEqualTo(after.BasisX) || !before.BasisY.IsAlmostEqualTo(after.BasisY) || !before.BasisZ.IsAlmostEqualTo(after.BasisZ))
                throw new Refusal("sync:unsupported", "MoveElement accepts translation only");
            if (S(c.Request["policy"]) == "refuse" && Ports(e).Any(p => Peers(p).Count > 0))
                throw new Refusal("sync:constrainedEnd", "Connected move refused by gap policy");
            if (S(c.Request["policy"]) == "disconnect") foreach (var port in Ports(e)) Disconnect(c, port);
            ElementTransformUtils.MoveElement(c.Doc, e.Id, after.Origin - before.Origin);
        }
        else if (name == "aeco:pipe:nominalDiameter")
        {
            // Revalidate here even if a caller bypassed Python and CheckEdits.
            double diameter = Feet(N(edit["value"]));
            if (!Segment((Pipe)e).GetSizes().Any(s => Math.Abs(s.NominalDiameter - diameter) < Feet(1e-9)))
                throw new Refusal("pipeSizeNotInTable", "Diameter is absent from the active PipeSegment table");
            if (!e.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM).Set(diameter))
                throw new Refusal("revit:parameterRefused", "Pipe diameter was not set");
        }
        else if (e is Wall wall)
        {
            if (name == "aeco:wall:flipped") { if (wall.Flipped != edit["value"].GetValue<bool>()) wall.Flip(); }
            else if (name == "aeco:wall:locationLine")
            {
                int line = Array.IndexOf(LocationLines, S(edit["value"]));
                if (line < 0) throw new Refusal("sync:unsupported", "Unknown wall location line");
                if (!wall.get_Parameter(BuiltInParameter.WALL_KEY_REF_PARAM).Set(line)) throw new Refusal("revit:parameterRefused", "Wall location line was not set");
            }
            else
            {
                var parameter = name == "aeco:wall:height" ? BuiltInParameter.WALL_USER_HEIGHT_PARAM : name == "aeco:wall:baseOffset" ? BuiltInParameter.WALL_BASE_OFFSET : BuiltInParameter.WALL_TOP_OFFSET;
                var p = wall.get_Parameter(parameter);
                if (p == null || p.IsReadOnly || !p.Set(Feet(N(edit["value"])))) throw new Refusal("revit:parameterRefused", "Wall parameter is constrained or read-only");
            }
        }
        c.Doc.Regenerate();
    }
    public static void CheckGaps(Context c, IEnumerable<Element> elements)
    {
        foreach (var e in elements)
            if (!IsCamera(e))
            foreach (var port in Ports(e))
                foreach (var peer in Peers(port))
                    if (port.Origin.DistanceTo(peer.Origin) > Feet(1e-4))
                        throw new Refusal("sync:gap", "Connected port gap after commit: " + PortRef(port) + " / " + PortRef(peer));
    }
}
