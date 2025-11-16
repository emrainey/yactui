# Cyphal TUI

A 'textual' based TUI for monitoring Cyphal networks.

## Usage

### Environment

This is the contents of the .env I use. I've setup a `cyphal` folder in my home directory which has the [UAVCAN Data Types](https://github.com/OpenCyphal/public_regulated_data_types) in the `dsdl` folder and I've
precompiled them to the `generated` folder.

```bash
export CYPHAL_PATH=$HOME/cyphal/dsdl
export YAKUT_PATH=$HOME/cyphal/generated
export PYTHONPATH=$HOME/cyphal/generated
```

No need to set `UAVCAN__NODE__ID` or `UAVCAN__UDP__IFACE`.

#### Precompiling

```bash
# Using yakut <= 0.13.0
yakut -v compile -O ${YAKUT_PATH} ${CYPHAL_PATH}/uavcan ${CYPHAL_PATH}/reg ${CYPHAL_PATH}/udral
```

### Command Options

```bash
$ yactui --help
Usage: yactui [-h] [-v] [--node-id NODE_ID] [--interface INTERFACE] [--ip IP] [--mtu MTU] [--cyphal-path CYPHAL_PATH] [--gen-path GEN_PATH]

Cyphal TUI Application

Options:
  -h, --help            show this help message and exit
  -v, --verbose         Increase verbosity level
  --node-id NODE_ID     The Node ID for this TUI instance in the Cyphal network default=0
  --interface INTERFACE
                        The network interface to bind to for Cyphal communication default=lo
  --ip IP               The IP address to bind to for Cyphal communication default=127.0.0.1
  --mtu MTU             The MTU to use for Cyphal communication default=1448
  --cyphal-path CYPHAL_PATH
                        The path to the Cyphal generated types default=/$HOME/cyphal/dsdl
  --gen-path GEN_PATH   The generated path for the Cyphal generated types default=$HOME/cyphal/generated
```

## Known Issues

* `--cyphal-path` Doesn't work quite right as the path is needed during import time, not arg parsing time.

## TODO

* Test CAN interfaces
* Test Serial interfaces (if they exist)
* Test 2 or more interface together
