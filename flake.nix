{
  description = "Revit sync host integration";
  inputs = {
    toolchain.url = "github:criad-com/usdaeco-toolchain?ref=v0.3.8";
    core.url = "github:criad-com/usdaeco-core?ref=v0.9.2";
    core.flake = false;
    axis.url = "github:criad-com/usdaeco-axis?ref=v0.1.2";
    axis.flake = false;
    sync.url = "github:criad-com/usdaeco-sync?ref=v0.5.2";
    sync.flake = false;
    datacentre.url = "github:criad-com/usdaeco-datacentre?ref=v0.4.6";
    datacentre.flake = false;
    scenarios.url = "github:criad-com/usdaeco-scenarios?ref=v0.6.0";
    scenarios.flake = false;
    cctv.url = "github:criad-com/usdaeco-cctv?ref=v0.5.2";
    cctv.flake = false;
    buildup.url = "github:criad-com/usdaeco-buildup?ref=v0.2.1";
    buildup.flake = false;
    wall.url = "github:criad-com/usdaeco-wall?ref=v0.2.1";
    wall.flake = false;
    pipe.url = "github:criad-com/usdaeco-pipe?ref=v0.2.1";
    pipe.flake = false;
    ifc.url = "github:criad-com/usdaeco-ifc?ref=v0.2.0";
    ifc.flake = false;
    nixpkgs.follows = "toolchain/nixpkgs";
  };
  outputs = { self, nixpkgs, toolchain, core, axis, sync, datacentre, scenarios, cctv, buildup, wall, pipe, ifc }:
    let
      each = nixpkgs.lib.genAttrs [ "aarch64-darwin" "x86_64-linux" ];
      make = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          kit = toolchain.lib.forSystem system;
          setup = ''
            export TOOLCHAIN_DIR=${toolchain}
            export AECO_IFC_ROOT=${ifc}
            export AECO_CORE=${core}
            export CORE_PLUGIN_DIR=${core}/usdAeco
            export AECO_AXIS_ROOT=${axis}
            export AXIS_PLUGIN_DIR=${axis}/usdAecoAxis
            export PXR_PLUGINPATH_NAME="$CORE_PLUGIN_DIR:$AXIS_PLUGIN_DIR"
            export AECO_CORE_ROOT=${core}
            export AECO_SYNC_ROOT=${sync}
            export AECO_DATACENTRE_ROOT=${datacentre}
            export AECO_SCENARIOS_ROOT=${scenarios}
            export AECO_CCTV_ROOT=${cctv}
            export AECO_BUILDUP_ROOT=${buildup}
            export AECO_WALL_ROOT=${wall}
            export AECO_PIPE_ROOT=${pipe}
          '';
          check = pkgs.runCommand "usdaeco-revit-check" {
            nativeBuildInputs = [ kit.pythonEnv kit.usd-dev ];
          } (setup + ''
            cp -R ${self} source
            chmod -R u+w source
            cd source
            env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" python check.py
            mkdir -p "$out"
          '');
          example = pkgs.writeShellApplication {
            name = "example";
            runtimeInputs = [ kit.pythonEnv kit.usd-dev ];
            text = setup + ''
              cp -R ${self} example-work
              chmod -R u+w example-work
              env -u PYTHONPATH python example-work/examples/roundtrip/run.py "$@"
            '';
          };
        in { inherit pkgs kit setup check example; };
    in {
      packages = each (system: let p = make system; in { default = p.pkgs.runCommand "usdaeco-revit-source" {} ''mkdir -p "$out"; cp -R ${self}/. "$out/"''; });
      checks = each (system: let p = make system; in { integration = p.check; });
      devShells = each (system: let p = make system; in { default = p.pkgs.mkShell { packages = [ p.kit.pythonEnv p.kit.usd-dev ]; shellHook = p.setup + "unset PYTHONPATH"; }; });
      apps = each (system: let p = make system; in { example = { type = "app"; program = "${p.example}/bin/example"; }; });
    };
}
