static partial class AecoRevit
{
    public static bool CameraSymbol(FamilySymbol symbol) => symbol != null && symbol.Category?.Id.Value == (long)BuiltInCategory.OST_SecurityDevices
        && (symbol.LookupParameter("AXIS Model") != null || symbol.LookupParameter("FOV Distance to Object") != null);
    public static bool IsCamera(Element e) => e is FamilyInstance fi && fi.SuperComponent == null
        && fi.Category?.Id.Value == (long)BuiltInCategory.OST_SecurityDevices
        && (CameraSymbol(fi.Symbol) || fi.LookupParameter("FOV Distance to Object") != null);
    public static object CameraValue(Parameter p)
    {
        if (p.StorageType != StorageType.Double) return ParameterValue(p);
        var spec = p.Definition.GetDataType();
        if (spec == SpecTypeId.Length) return Metres(p.AsDouble());
        if (spec == SpecTypeId.Angle) return p.AsDouble() * 180.0 / Math.PI;
        return p.AsDouble(); // focal lengths are Number parameters in millimetres
    }
    public static Dictionary<string, object> CameraParameters(Element e)
    {
        var names = new[] { "AXIS Model", "AXIS Category", "Corridor Format", "Detect", "Observe", "Recognize", "Identify", "User-defined Pixel Density", "Scenario",
            "Origin Horizontal", "Origin Vertical", "Placement ID", "Mounting Type", "Pendant Length", "Telescopic Mount Length", "Tilt Min", "Tilt Max" };
        var result = D();
        foreach (Parameter p in e.Parameters)
            if (p.HasValue && (p.Definition.Name.StartsWith("FOV ") || p.Definition.Name.StartsWith("Preset ") || names.Contains(p.Definition.Name)))
                result[p.Definition.Name] = CameraValue(p);
        return result;
    }
    public static IEnumerable<FamilyInstance> CameraChildren(FamilyInstance camera)
    {
        foreach (var id in camera.GetSubComponentIds().OrderBy(id => id.Value))
            if (camera.Document.GetElement(id) is FamilyInstance child)
            {
                yield return child;
                foreach (var nested in CameraChildren(child)) yield return nested;
            }
    }
    public static void CameraRecord(Context c, FamilyInstance camera, Dictionary<string, object> row)
    {
        row["kind"] = "camera";
        row["mark"] = camera.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString() ?? ""; // label, separate from IFC_GUID
        row["ports"] = new object[0]; // electrical connectors are outside the pipe port contract
        row["location"] = Point(((LocationPoint)camera.Location).Point);
        var frame = camera.GetTransform();
        row["rotation"] = Math.Atan2(frame.BasisX.Y, frame.BasisX.X) * 180.0 / Math.PI;
        row["mirrored"] = camera.Mirrored;
        var level = c.Doc.GetElement(camera.LevelId) as Level;
        row["level"] = level?.Name ?? "";
        if (level != null)
        {
            var binding = BindingOf(c, level);
            binding["matrix"] = Matrix(Transform.CreateTranslation(new XYZ(0, 0, level.Elevation)));
            row["levelBinding"] = binding;
        }
        var offset = camera.LookupParameter("Offset from Host") ?? camera.get_Parameter(BuiltInParameter.INSTANCE_ELEVATION_PARAM);
        row["offsetFromHost"] = offset == null ? 0.0 : Metres(offset.AsDouble());
        row["typeName"] = camera.Symbol.Name;
        row["cameraParameters"] = CameraParameters(camera);
        row["typeParameters"] = CameraParameters(camera.Symbol);
        var children = new List<Dictionary<string, object>>();
        foreach (var child in CameraChildren(camera))
        {
            bool fov = child.LookupParameter("Horizontal Angle") != null;
            bool symbol = child.Symbol.Family.Name.IndexOf("2D Symbol", StringComparison.OrdinalIgnoreCase) >= 0;
            if (!fov && !symbol) continue;
            var record = D("role", fov ? "fov" : "symbol", "subId", LocalId(child.Id), "uniqueId", child.UniqueId,
                "matrix", Matrix(frame.Inverse.Multiply(child.GetTransform())));
            var parameters = D();
            foreach (string name in new[] { "Horizontal Angle", "Vertical Angle", "RG_Length_Det", "RG_Length_Obs", "RG_Length_Rec", "RG_Length_Id", "RG_Length_UD", "XEff",
                "T_Res_Det", "T_Res_Obs", "T_Res_Rec", "T_Res_Id", "T_Res_UD", "Focal Length", "Tilt", "FOV Distance to Object", "Horizontal Res", "Vertical Res", "Visible" })
            {
                var p = child.LookupParameter(name);
                if (p == null || !p.HasValue) continue;
                parameters[name] = name == "Horizontal Angle" || name == "Vertical Angle" ? p.AsDouble()
                    : name == "XEff" && p.Definition.GetDataType() == SpecTypeId.Length ? Metres(p.AsDouble()) * 1000.0 : CameraValue(p);
            }
            record["parameters"] = parameters;
            children.Add(record);
        }
        row["subInstances"] = children;
    }
    public static bool CameraGuide(GeometryObject item, Document doc)
    {
        var style = doc.GetElement(item.GraphicsStyleId) as GraphicsStyle;
        string name = style?.GraphicsStyleCategory?.Name ?? "";
        return name.IndexOf("Field of View", StringComparison.OrdinalIgnoreCase) >= 0
            || name.IndexOf("Resolution Guide", StringComparison.OrdinalIgnoreCase) >= 0
            || name.StartsWith("FOV", StringComparison.OrdinalIgnoreCase);
    }
    public static string CameraParameter(FamilyInstance camera, JsonNode edit)
    {
        string name = S(edit["name"]);
        if (name == "aeco:cctv:scenario") return "Scenario";
        var parts = name.Split(':');
        string field = parts.Last();
        int head = edit["section"]?["nativeIndex"]?.GetValue<int>() ?? 1;
        bool preset = name.StartsWith("aeco:cctvPreset:");
        if (preset && (!Int32.TryParse(parts[2].Replace("Preset_", ""), out head) || head < 1 || head > 4))
            throw new Refusal("sync:unsupported", "Revit presets must be named Preset_1 through Preset_4");
        string perHead = "FOV " + head + " ";
        string[] options = field == "pan" ? new[] { perHead + "Pan", perHead + "Camera Rotation", "FOV Pan", "FOV Camera Rotation" }
            : field == "tilt" ? new[] { perHead + "Tilt", perHead + "Camera Tilt", "FOV Tilt", "FOV Camera Tilt" }
            : field == "focalLength" ? new[] { perHead + "Desired Focal Length", "FOV Desired Focal Length" }
            : field == "range" ? new[] { perHead + "Distance to Object", "FOV Distance to Object" }
            : field == "targetDensity" ? new[] { perHead + "Target Pixel Density", "FOV Target Pixel Density" }
            : field == "roll" ? new[] { "Corridor Format" } : new string[0];
        // Prefer base PTZ controls; families with only numbered controls use head 1.
        bool ptz = camera.LookupParameter("Preset 1") != null;
        if (!preset && ptz) options = options.OrderBy(n => n.StartsWith(perHead) ? 1 : 0).ToArray();
        if (preset) options = options.Where(n => n.StartsWith(perHead)).ToArray();
        return options.FirstOrDefault(n => camera.LookupParameter(n) != null)
            ?? throw new Refusal("sync:unsupported", "Camera family does not expose " + name);
    }
    public static void CheckCameraFocal(FamilySymbol symbol, double focal)
    {
        double lo = symbol.LookupParameter("FOV Focal Length Minimum")?.AsDouble() ?? Double.NaN;
        double hi = symbol.LookupParameter("FOV Focal Length Maximum")?.AsDouble() ?? Double.NaN;
        if (!Double.IsFinite(focal) || !Double.IsFinite(lo) || !Double.IsFinite(hi) || lo <= 0 || hi < lo || focal != 0 && (focal < lo || focal > hi))
            throw new Refusal("cctvOutOfEnvelope", "Focal length is outside the camera type envelope");
    }
    public static FamilySymbol CameraTargetSymbol(Context c, FamilyInstance camera)
    {
        var swap = c.Request["edits"]?.AsArray().FirstOrDefault(e => S(e["ref"]) == camera.UniqueId
            && S(e["operation"]) == "primMetadata" && S(e["name"]) == "inheritPaths");
        if (swap == null) return camera.Symbol;
        var paths = swap["value"].AsArray();
        if (paths.Count != 1 || !(Lookup(c, S(paths[0])) is FamilySymbol symbol) || !CameraSymbol(symbol))
            throw new Refusal("sync:unsupported", "Camera type swap needs one bound camera FamilySymbol");
        return symbol;
    }
    public static void CheckCameraFinalFocals(FamilyInstance camera)
    {
        foreach (Parameter focal in camera.Parameters)
            if (focal.HasValue && focal.Definition.Name.EndsWith("Desired Focal Length"))
                CheckCameraFocal(camera.Symbol, focal.AsDouble());
    }
    public static void CheckCameraEdit(Context c, FamilyInstance camera, JsonNode edit)
    {
        string name = S(edit["name"]), operation = S(edit["operation"]);
        if (operation == "activation" && !edit["value"].GetValue<bool>()) return;
        if (operation == "primMetadata" && name == "inheritPaths")
        {
            var paths = edit["value"].AsArray();
            if (paths.Count != 1 || !(Lookup(c, S(paths[0])) is FamilySymbol symbol) || !CameraSymbol(symbol))
                throw new Refusal("sync:unsupported", "Camera type swap needs one bound camera FamilySymbol");
            // Across families, the new instance controls are inspected after the swap.
            if (camera.Symbol.Family.Id == symbol.Family.Id)
            foreach (Parameter focal in camera.Parameters)
                if (focal.HasValue && focal.Definition.Name.EndsWith("Desired Focal Length"))
                {
                    var pending = c.Request["edits"]?.AsArray().FirstOrDefault(other => S(other["ref"]) == camera.UniqueId
                        && S(other["name"]).EndsWith(":focalLength") && CameraParameter(camera, other) == focal.Definition.Name);
                    CheckCameraFocal(symbol, pending == null ? focal.AsDouble() : N(pending["value"]));
                }
            return;
        }
        if (operation != "attribute") throw new Refusal("sync:unsupported", "Unsupported camera operation");
        if (name == "xformOpOrder")
        {
            if (edit["value"].AsArray().Count != 1 || S(edit["value"][0]) != "xformOp:transform") throw new Refusal("sync:unsupported", "Use one camera matrix transform");
            return;
        }
        if (name == "xformOp:transform") return;
        var target = CameraTargetSymbol(c, camera);
        if (name.EndsWith(":focalLength")) CheckCameraFocal(target, N(edit["value"]));
        if (name.EndsWith(":roll") && N(edit["value"]) != 0 && N(edit["value"]) != 90)
            throw new Refusal("sync:unsupported", "Corridor Format can represent only roll 0 or 90 degrees");
        // Preflight sees the old family. ApplyCameraEdit checks again after the
        // type-first transaction has installed and regenerated the target symbol.
        if (target.Id != camera.Symbol.Id) return;
        var p = camera.LookupParameter(CameraParameter(camera, edit));
        if (p == null || p.IsReadOnly) throw new Refusal("revit:parameterRefused", "Camera parameter is absent or read-only");
    }
    public static void MoveCamera(Context c, FamilyInstance camera, Transform after)
    {
        var before = camera.GetTransform();
        if (!after.BasisZ.IsAlmostEqualTo(XYZ.BasisZ) || Math.Abs(after.Determinant - before.Determinant) > 1e-9
            || Math.Abs(after.BasisX.GetLength() - 1) > 1e-9 || Math.Abs(after.BasisY.GetLength() - 1) > 1e-9
            || Math.Abs(after.BasisX.DotProduct(after.BasisY)) > 1e-9)
            throw new Refusal("sync:unsupported", "Camera placement requires a rigid Z rotation with its existing mirror state");
        ElementTransformUtils.MoveElement(c.Doc, camera.Id, after.Origin - ((LocationPoint)camera.Location).Point);
        double delta = Math.Atan2(after.BasisX.Y, after.BasisX.X) - Math.Atan2(before.BasisX.Y, before.BasisX.X);
        var vertical = Line.CreateUnbound(((LocationPoint)camera.Location).Point, XYZ.BasisZ);
        if (Math.Abs(delta) > 1e-12) ElementTransformUtils.RotateElement(c.Doc, camera.Id, vertical, delta);
    }
    public static void ApplyCameraEdit(Context c, FamilyInstance camera, JsonNode edit)
    {
        CheckCameraEdit(c, camera, edit); // direct script callers cannot bypass envelope checks
        string name = S(edit["name"]), operation = S(edit["operation"]);
        if (operation == "activation") { c.Doc.Delete(camera.Id); return; }
        if (operation == "primMetadata")
        {
            var symbol = (FamilySymbol)Lookup(c, S(edit["value"][0]));
            if (!symbol.IsActive) symbol.Activate();
            camera.Symbol = symbol;
            c.Doc.Regenerate();
            return;
        }
        if (name == "xformOpOrder") return;
        if (name == "xformOp:transform")
        {
            var binding = c.Bindings[S(edit["path"])];
            MoveCamera(c, camera, FromMatrix(binding["parentMatrix"]).Multiply(FromMatrix(edit["value"])));
            return;
        }
        var parameter = camera.LookupParameter(CameraParameter(camera, edit));
        bool set;
        if (parameter.StorageType == StorageType.String) set = parameter.Set(S(edit["value"]));
        else
        {
            double val = N(edit["value"]);
            if (name.EndsWith(":focalLength") && val == 0) val = camera.Symbol.LookupParameter("FOV Focal Length Minimum").AsDouble();
            if (name.EndsWith(":roll")) val = val == 90 ? 1 : 0;
            if (parameter.Definition.GetDataType() == SpecTypeId.Angle) val *= Math.PI / 180.0;
            if (parameter.Definition.GetDataType() == SpecTypeId.Length) val = Feet(val);
            if (parameter.StorageType == StorageType.Integer && val != Math.Truncate(val)) throw new Refusal("sync:unsupported", "Native camera parameter requires an integer");
            set = parameter.StorageType == StorageType.Integer ? parameter.Set((int)val) : parameter.Set(val);
        }
        if (!set) throw new Refusal("revit:parameterRefused", "Camera parameter Set returned false");
        if (name.StartsWith("aeco:cctvPreset:"))
        {
            string number = name.Split(':')[2].Replace("Preset_", "");
            var enabled = camera.LookupParameter("Preset " + number);
            if (enabled == null || enabled.IsReadOnly || !enabled.Set(1)) throw new Refusal("revit:parameterRefused", "Cannot enable camera preset");
        }
    }
    public static void CreateCamera(Context c, JsonNode edit)
    {
        var data = edit["value"];
        var symbol = Lookup(c, S(data["inherits"][0])) as FamilySymbol;
        string path = S(edit["path"]), parent = path.Substring(0, path.LastIndexOf('/'));
        string levelPath = S(data["levelPath"]);
        if (String.IsNullOrEmpty(levelPath)) throw new Refusal("sync:unbound", "Camera creation lacks its USD AecoLevel ancestor");
        var level = Lookup(c, levelPath) as Level;
        if (level == null || !CameraSymbol(symbol)) throw new Refusal("sync:unsupported", "New camera needs a bound level and camera symbol");
        foreach (var sensor in data["sensors"].AsArray())
            foreach (var pair in sensor["drivers"].AsObject())
                if (pair.Key.EndsWith(":focalLength")) CheckCameraFocal(symbol, N(pair.Value));
        if (!symbol.IsActive) { symbol.Activate(); c.Doc.Regenerate(); }
        var local = data["attributes"]["xformOp:transform"];
        var matrix = local == null ? Transform.Identity : FromMatrix(local);
        if (data["parentMatrix"] != null) matrix = FromMatrix(data["parentMatrix"]).Multiply(matrix);
        else if (c.Bindings[parent]?["matrix"] != null) matrix = FromMatrix(c.Bindings[parent]["matrix"]).Multiply(matrix);
        var camera = c.Doc.Create.NewFamilyInstance(matrix.Origin, symbol, level, Autodesk.Revit.DB.Structure.StructuralType.NonStructural);
        MoveCamera(c, camera, matrix);
        if (data["attributes"]["aeco:id"] != null)
        {
            var id = camera.get_Parameter(BuiltInParameter.IFC_GUID);
            if (id == null || id.IsReadOnly || !id.Set(IfcCompress(Guid.Parse(S(data["attributes"]["aeco:id"])))))
                throw new Refusal("revit:identityPersistence", "Cannot persist the requested camera identity");
        }
        if (data["mark"] != null)
        {
            var mark = camera.get_Parameter(BuiltInParameter.ALL_MODEL_MARK);
            if (mark == null || mark.IsReadOnly || !mark.Set(S(data["mark"])))
                throw new Refusal("revit:parameterRefused", "Cannot persist the camera Mark label");
        }
        c.Paths[camera.UniqueId] = path;
        foreach (var pair in data["attributes"].AsObject().Where(p => p.Key.StartsWith("aeco:cctv:")))
            ApplyCameraEdit(c, camera, new JsonObject { ["operation"] = "attribute", ["name"] = pair.Key, ["value"] = pair.Value.DeepClone() });
        foreach (var sensor in data["sensors"].AsArray())
            foreach (var pair in sensor["drivers"].AsObject())
                ApplyCameraEdit(c, camera, new JsonObject { ["operation"] = "attribute", ["name"] = pair.Key, ["value"] = pair.Value.DeepClone(),
                    ["section"] = new JsonObject { ["nativeIndex"] = Int32.Parse(S(sensor["name"]).Split('_').Last()) + 1 } });
        c.Changed.Add(camera.Id); c.Added.Add(camera.Id);
        c.Doc.Regenerate();
    }
}
