{ config, lib, pkgs, ... }:
let
  cfg = config.services.wayswitch;
in
{
  options.services.wayswitch = {
    enable = lib.mkEnableOption "WaySwitch in graphical user sessions";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
      description = "WaySwitch package providing its user service and udev rules.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];
    boot.kernelModules = [ "uinput" ];
    services.udev.packages = [ cfg.package ];
    services.gnome.at-spi2-core.enable = true;
    programs.dconf.enable = true;
    programs.dconf.profiles.user.databases = [{
      settings."org/gnome/desktop/interface".toolkit-accessibility = true;
    }];
    systemd.packages = [ cfg.package ];
    systemd.user.services.wayswitch = {
      overrideStrategy = "asDropin";
      wantedBy = [ "graphical-session.target" ];
    };
  };
}
