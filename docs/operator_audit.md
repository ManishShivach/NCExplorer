# Operator surface audit

NCExplorer lets you reach a CDO operator from three places: the toolbar's category menus, the command palette (Ctrl+K) and the model builder's palette. Nothing in the code forces those three to offer the same operators, or to ask for the same parameters when they do. This report is the check.

`audit_operator_surfaces.py` builds the real widgets, reads back what each one offers, and compares all three against the operator list the installed CDO prints. Every number below was measured that way, so the report is evidence rather than a description of the code that produced it.

Re-run it after any change to the catalog, the menus or the parameter forms:

```bash
QT_QPA_PLATFORM=offscreen python audit_operator_surfaces.py
```

It exits non-zero when a surface disagrees with the others or offers an operator the installed CDO cannot run, so it can gate a release.

- CDO tested: `/usr/local/bin/cdo` — Climate Data Operators version 2.6.3 (https://mpimet.mpg.de/cdo)
- Report written: 2026-08-18 01:25

## What was found

- Installed CDO operators (`cdo --operators`): **943**
- Reachable from the toolbar menus: **943**
- Indexed by the command palette (Ctrl+K): **943**
- Offered by the model builder: **943**
- Disagreements about which operators exist: **0**
- Disagreements about an operator's parameters: **0**
- Disagreements about an operator's arity: **0**

All four sets are identical: every installed operator is reachable from all three surfaces, and no surface offers an operator the installed CDO cannot run.

The last two counts are the ones a user pays for. A surface that offers the right operator but the wrong parameters draws a form CDO answers with "Argument parse error!"; one that is wrong about how many input files an operator takes builds a command CDO refuses with "Missing inputs". Neither message names the surface that caused it, which is why they are counted here rather than left to a bug report. `core/categories.py` holds the intended answer in both cases.

## Per-category totals

How the catalog is spread across the sixteen category menus. Each menu shows a short list of common operators first and keeps the remainder one click away under **All …**, so no menu is 289 items long.

| Category | Top level | Behind “All …” | Total |
|---|---:|---:|---:|
| Information | 10 | 72 | 82 |
| File operations | 10 | 27 | 37 |
| Selection | 10 | 34 | 44 |
| Conditional selection | 6 | 0 | 6 |
| Comparison | 10 | 14 | 24 |
| Modification | 10 | 62 | 72 |
| Arithmetic | 10 | 68 | 78 |
| Statistical values | 10 | 279 | 289 |
| Correlation | 4 | 0 | 4 |
| EOFs | 8 | 0 | 8 |
| Regression | 5 | 0 | 5 |
| Interpolation | 10 | 48 | 58 |
| Transformation | 10 | 8 | 18 |
| Import/Export | 10 | 19 | 29 |
| Graphics | 6 | 0 | 6 |
| Miscellaneous | 10 | 111 | 121 |
| ECA indices | 10 | 52 | 62 |
| **Total** | **149** | **794** | **943** |

## Every operator

One row per operator, sorted by name. What the columns mean:

| Column | Reads as |
|---|---|
| `Sig` | input files → output files. `n` means "any number"; `1→0` is an operator that prints to the terminal instead of writing a file. |
| `Params` | how many parameters the form asks for. `0` means the operator runs on the file alone. |
| `Syntax` | the command shape, as CDO documents it. `[,x]` is optional; `x=<type>` is a keyword parameter rather than a positional one. |
| `Placement` | where the operator sits in its category menu. *top* is a direct click, *top (curated)* one of the entries promoted for being commonly used, *All…* the submenu holding the rest of the category. |
| `Menu` `Palette` `Builder` | ticked when the live widget really offers it. Three ticks on every row is the result this audit exists to confirm. |

| # | Operator | Category | Sig | Params | Syntax | Placement | Menu | Palette | Builder |
|---:|---|---|---|---:|---|---|:-:|:-:|:-:|
| 1 | `abs` | Arithmetic | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 2 | `acos` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 3 | `add` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 4 | `addc` | Arithmetic | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 5 | `addtrend` | Regression | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile [,equal=true]` | top (curated) | ✓ | ✓ | ✓ |
| 6 | `adipot` | Miscellaneous | 1→1 | 1 | `ifile ofile [,pressure]` | All… | ✓ | ✓ | ✓ |
| 7 | `adisit` | Miscellaneous | 1→1 | 1 | `ifile ofile [,pressure]` | All… | ✓ | ✓ | ✓ |
| 8 | `aexpr` | Arithmetic | 1→1 | 1 | `ifile ofile instr` | All… | ✓ | ✓ | ✓ |
| 9 | `aexprf` | Arithmetic | 1→1 | 1 | `ifile ofile filename` | All… | ✓ | ✓ | ✓ |
| 10 | `after` | Miscellaneous | n→1 | 1 | `ifiles ofile [,vct]` | All… | ✓ | ✓ | ✓ |
| 11 | `afterburner` | Miscellaneous | n→1 | 1 | `ifiles ofile [,vct]` | All… | ✓ | ✓ | ✓ |
| 12 | `air_density` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 13 | `anomaly` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 14 | `ap2hl` | Interpolation | 1→1 | 1 | `ifile ofile hlevels` | All… | ✓ | ✓ | ✓ |
| 15 | `ap2hlx` | Interpolation | 1→1 | 1 | `ifile ofile hlevels` | All… | ✓ | ✓ | ✓ |
| 16 | `ap2pl` | Interpolation | 1→1 | 1 | `ifile ofile plevels` | All… | ✓ | ✓ | ✓ |
| 17 | `ap2plx` | Interpolation | 1→1 | 1 | `ifile ofile plevels` | All… | ✓ | ✓ | ✓ |
| 18 | `arg` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 19 | `asin` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 20 | `atan` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 21 | `atan2` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 22 | `bandpass` | Miscellaneous | 1→1 | 2 | `ifile ofile fmin,fmax` | All… | ✓ | ✓ | ✓ |
| 23 | `bitrounding` | File operations | 1→1 | 8 | `ifile ofile [,inflevel=<float>][,addbits=<int>][,minbits=<int>][,maxbits=<int>][,numsteps=<int>][,numbits=<int>][,printbits=true][,filename=<file>]` | All… | ✓ | ✓ | ✓ |
| 24 | `bottomvalue` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 25 | `boxavg` | Miscellaneous | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 26 | `cat` | File operations | n→1 | 0 | `ifiles ofile` | top (curated) | ✓ | ✓ | ✓ |
| 27 | `cdiread` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 28 | `cdiwrite` | Miscellaneous | 0→1 | 3 | `ofile [,grid=<grid>][,nlevs=<int>][,nvars=<int>]` | All… | ✓ | ✓ | ✓ |
| 29 | `changemulti` | Modification | 1→1 | 1 | `ifile ofile params` | All… | ✓ | ✓ | ✓ |
| 30 | `chcode` | Modification | 1→1 | 1 | `ifile ofile pairs` | top (curated) | ✓ | ✓ | ✓ |
| 31 | `chlevel` | Modification | 1→1 | 1 | `ifile ofile pairs` | top (curated) | ✓ | ✓ | ✓ |
| 32 | `chlevelc` | Modification | 1→1 | 3 | `ifile ofile code,oldlev,newlev` | top (curated) | ✓ | ✓ | ✓ |
| 33 | `chlevelv` | Modification | 1→1 | 3 | `ifile ofile var,oldlev,newlev` | top (curated) | ✓ | ✓ | ✓ |
| 34 | `chltype` | Modification | 1→1 | 2 | `ifile ofile oldtype,newtype` | All… | ✓ | ✓ | ✓ |
| 35 | `chname` | Modification | 1→1 | 1 | `ifile ofile pairs` | top (curated) | ✓ | ✓ | ✓ |
| 36 | `chparam` | Modification | 1→1 | 1 | `ifile ofile pairs` | All… | ✓ | ✓ | ✓ |
| 37 | `chtabnum` | Modification | 1→1 | 2 | `ifile ofile oldtab,newtab` | All… | ✓ | ✓ | ✓ |
| 38 | `chunit` | Modification | 1→1 | 1 | `ifile ofile pairs` | All… | ✓ | ✓ | ✓ |
| 39 | `chvar` | Modification | 1→1 | 1 | `ifile ofile pairs` | All… | ✓ | ✓ | ✓ |
| 40 | `cinfo` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 41 | `clone` | File operations | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 42 | `cloudlayer` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 43 | `cmor` | Import/Export | 1→0 | 23 | `ifile MIPtable[,cmor_name=<string>][,name=<string>][,code=<int>][,info=<string>][,grid_info=<file>][,mapping_table=<file>][,keep_all_attributes=<select>][,drs=<select>][,drs_root=<string>][,output_mode=<select>][,last_chunk=<file>][,max_size=<int>][,deflate_level=<int>][,version_date=<int>][,required_time_units=<string>][,cell_methods=<select>][,units=<string>][,variable_comment=<string>][,positive=<select>][,z_axis=<string>][,character_axis=<select>][,t_axis=<select>]` | top | ✓ | ✓ | ✓ |
| 44 | `cmorlite` | Miscellaneous | 1→1 | 2 | `ifile ofile table[,convert]` | All… | ✓ | ✓ | ✓ |
| 45 | `codetab` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 46 | `collgrid` | File operations | n→1 | 4 | `ifiles ofile [,nx=<int>][,name=<string>][,levidx=<string>][,gridtype=<select>]` | All… | ✓ | ✓ | ✓ |
| 47 | `complextopol` | Miscellaneous | 1→2 | 0 | `ifile ofile1 ofile2` | All… | ✓ | ✓ | ✓ |
| 48 | `complextorect` | Miscellaneous | 1→2 | 0 | `ifile ofile1 ofile2` | All… | ✓ | ✓ | ✓ |
| 49 | `conj` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 50 | `consecsum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 51 | `consects` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 52 | `const` | Miscellaneous | 0→1 | 2 | `ofile const,grid` | top (curated) | ✓ | ✓ | ✓ |
| 53 | `contour` | Graphics | 1→1 | 17 | `ifile obase [,device=<select>][,projection=<select>][,style=<select>][,min=<float>][,max=<float>][,lon_min=<float>][,lon_max=<float>][,lat_min=<float>][,lat_max=<float>][,count=<int>][,interval=<float>][,list=<string>][,RGB=true][,step_freq=<int>][,file_split=true][,colour=<select>][,thickness=<float>]` | top | ✓ | ✓ | ✓ |
| 54 | `conv_cmor_table` | Information | 0→0 | 0 | `` | All… | ✓ | ✓ | ✓ |
| 55 | `copy` | File operations | n→1 | 0 | `ifiles ofile` | top (curated) | ✓ | ✓ | ✓ |
| 56 | `cos` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 57 | `coshill` | Miscellaneous | 0→1 | 0 | `ofile` | All… | ✓ | ✓ | ✓ |
| 58 | `dayadd` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 59 | `dayavg` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | top (curated) | ✓ | ✓ | ✓ |
| 60 | `daycount` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 61 | `daydiv` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 62 | `daymax` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | top (curated) | ✓ | ✓ | ✓ |
| 63 | `daymean` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | top (curated) | ✓ | ✓ | ✓ |
| 64 | `daymin` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | top (curated) | ✓ | ✓ | ✓ |
| 65 | `daymul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 66 | `daypctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | top (curated) | ✓ | ✓ | ✓ |
| 67 | `dayrange` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 68 | `daystd` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | top (curated) | ✓ | ✓ | ✓ |
| 69 | `daystd1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 70 | `daysub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 71 | `daysum` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | top (curated) | ✓ | ✓ | ✓ |
| 72 | `dayvar` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | top (curated) | ✓ | ✓ | ✓ |
| 73 | `dayvar1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 74 | `dcw` | Information | 0→0 | 0 | `` | All… | ✓ | ✓ | ✓ |
| 75 | `del29feb` | Selection | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 76 | `delattribute` | Selection | 1→1 | 1 | `ifile ofile attrs` | All… | ✓ | ✓ | ✓ |
| 77 | `delcode` | Selection | 1→1 | 1 | `ifile ofile codes` | top (curated) | ✓ | ✓ | ✓ |
| 78 | `delday` | Selection | 1→1 | 1 | `ifile ofile day` | All… | ✓ | ✓ | ✓ |
| 79 | `delete` | File operations | n→1 | 1 | `ifiles ofile selection` | All… | ✓ | ✓ | ✓ |
| 80 | `delgridcell` | Selection | 1→1 | 1 | `ifile ofile cells` | All… | ✓ | ✓ | ✓ |
| 81 | `delmulti` | Selection | 1→1 | 1 | `ifile ofile params` | All… | ✓ | ✓ | ✓ |
| 82 | `delname` | Selection | 1→1 | 1 | `ifile ofile vars` | top (curated) | ✓ | ✓ | ✓ |
| 83 | `delparam` | Selection | 1→1 | 1 | `ifile ofile params` | All… | ✓ | ✓ | ✓ |
| 84 | `delta_pressure` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 85 | `deltat` | Miscellaneous | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 86 | `delvar` | Selection | 1→1 | 1 | `ifile ofile vars` | All… | ✓ | ✓ | ✓ |
| 87 | `detrend` | Regression | 1→1 | 1 | `ifile ofile [,equal=true]` | top (curated) | ✓ | ✓ | ✓ |
| 88 | `dhouravg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 89 | `dhourmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 90 | `dhourmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 91 | `dhourmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 92 | `dhourrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 93 | `dhourstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 94 | `dhourstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 95 | `dhoursum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 96 | `dhourvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 97 | `dhourvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 98 | `diff` | Information | 2→0 | 0 | `ifile1 ifile2` | top (curated) | ✓ | ✓ | ✓ |
| 99 | `diffc` | Information | 2→0 | 0 | `ifile1 ifile2` | All… | ✓ | ✓ | ✓ |
| 100 | `diffn` | Information | 2→0 | 0 | `ifile1 ifile2` | All… | ✓ | ✓ | ✓ |
| 101 | `diffp` | Information | 2→0 | 0 | `ifile1 ifile2` | All… | ✓ | ✓ | ✓ |
| 102 | `difftest` | Information | 2→0 | 0 | `ifile1 ifile2` | All… | ✓ | ✓ | ✓ |
| 103 | `diffv` | Information | 2→0 | 0 | `ifile1 ifile2` | top (curated) | ✓ | ✓ | ✓ |
| 104 | `distgrid` | File operations | 1→n | 2 | `ifile obase nx[,ny]` | All… | ✓ | ✓ | ✓ |
| 105 | `div` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 106 | `divc` | Arithmetic | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 107 | `divcoslat` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 108 | `divdpm` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 109 | `divdpy` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 110 | `dminuteavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 111 | `dminutemax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 112 | `dminutemean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 113 | `dminutemin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 114 | `dminuterange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 115 | `dminutestd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 116 | `dminutestd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 117 | `dminutesum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 118 | `dminutevar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 119 | `dminutevar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 120 | `dump_cmor_table` | Information | 0→0 | 0 | `` | All… | ✓ | ✓ | ✓ |
| 121 | `dumpmap` | Miscellaneous | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 122 | `duplicate` | File operations | 1→1 | 1 | `ifile ofile [,ndup]` | All… | ✓ | ✓ | ✓ |
| 123 | `dv2ps` | Transformation | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 124 | `dv2uv` | Transformation | 1→1 | 1 | `ifile ofile [,gridtype]` | top (curated) | ✓ | ✓ | ✓ |
| 125 | `dv2uvl` | Transformation | 1→1 | 1 | `ifile ofile [,gridtype]` | top (curated) | ✓ | ✓ | ✓ |
| 126 | `eca_cdd` | ECA indices | 1→1 | 3 | `ifile ofile [,R][,N][,freq]` | top (curated) | ✓ | ✓ | ✓ |
| 127 | `eca_cfd` | ECA indices | 1→1 | 1 | `ifile ofile [,N]` | top (curated) | ✓ | ✓ | ✓ |
| 128 | `eca_csu` | ECA indices | 1→1 | 2 | `ifile ofile [,T][,N]` | top (curated) | ✓ | ✓ | ✓ |
| 129 | `eca_cwd` | ECA indices | 1→1 | 3 | `ifile ofile [,R][,N][,freq]` | top (curated) | ✓ | ✓ | ✓ |
| 130 | `eca_cwdi` | ECA indices | 2→1 | 2 | `ifile1 ifile2 ofile [,nday][,T]` | top (curated) | ✓ | ✓ | ✓ |
| 131 | `eca_cwfi` | ECA indices | 2→1 | 2 | `ifile1 ifile2 ofile [,nday][,freq]` | top (curated) | ✓ | ✓ | ✓ |
| 132 | `eca_etr` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 133 | `eca_fd` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | top (curated) | ✓ | ✓ | ✓ |
| 134 | `eca_gsl` | ECA indices | 2→1 | 3 | `ifile1 ifile2 ofile [,nday][,T][,fland]` | top (curated) | ✓ | ✓ | ✓ |
| 135 | `eca_hd` | ECA indices | 1→1 | 2 | `ifile ofile [,T1][,T2]` | top (curated) | ✓ | ✓ | ✓ |
| 136 | `eca_hwdi` | ECA indices | 2→1 | 2 | `ifile1 ifile2 ofile [,nday][,T]` | All… | ✓ | ✓ | ✓ |
| 137 | `eca_hwfi` | ECA indices | 2→1 | 2 | `ifile1 ifile2 ofile [,nday][,freq]` | All… | ✓ | ✓ | ✓ |
| 138 | `eca_id` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | All… | ✓ | ✓ | ✓ |
| 139 | `eca_pd` | ECA indices | 1→1 | 1 | `ifile ofile x` | All… | ✓ | ✓ | ✓ |
| 140 | `eca_r10mm` | ECA indices | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 141 | `eca_r1mm` | ECA indices | 1→1 | 1 | `ifile ofile [,R]` | All… | ✓ | ✓ | ✓ |
| 142 | `eca_r20mm` | ECA indices | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 143 | `eca_r75p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 144 | `eca_r75ptot` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 145 | `eca_r90p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 146 | `eca_r90ptot` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 147 | `eca_r95p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 148 | `eca_r95ptot` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 149 | `eca_r99p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 150 | `eca_r99ptot` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 151 | `eca_rr1` | ECA indices | 1→1 | 1 | `ifile ofile [,R]` | All… | ✓ | ✓ | ✓ |
| 152 | `eca_rx1day` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | All… | ✓ | ✓ | ✓ |
| 153 | `eca_rx5day` | ECA indices | 1→1 | 2 | `ifile ofile [,x][,freq]` | All… | ✓ | ✓ | ✓ |
| 154 | `eca_sdii` | ECA indices | 1→1 | 1 | `ifile ofile [,R]` | All… | ✓ | ✓ | ✓ |
| 155 | `eca_su` | ECA indices | 1→1 | 2 | `ifile ofile [,T][,freq]` | All… | ✓ | ✓ | ✓ |
| 156 | `eca_tg10p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 157 | `eca_tg90p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 158 | `eca_tn10p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 159 | `eca_tn90p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 160 | `eca_tr` | ECA indices | 1→1 | 2 | `ifile ofile [,T][,freq]` | All… | ✓ | ✓ | ✓ |
| 161 | `eca_tx10p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 162 | `eca_tx90p` | ECA indices | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 163 | `enlarge` | Modification | 1→1 | 1 | `ifile ofile grid` | top (curated) | ✓ | ✓ | ✓ |
| 164 | `ensavg` | Statistical values | n→1 | 0 | `ifiles ofile` | top (curated) | ✓ | ✓ | ✓ |
| 165 | `ensbrs` | Statistical values | n→n | 1 | `ifiles obase x` | All… | ✓ | ✓ | ✓ |
| 166 | `enscrps` | Statistical values | n→n | 0 | `ifiles obase` | All… | ✓ | ✓ | ✓ |
| 167 | `enskurt` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 168 | `ensmax` | Statistical values | n→1 | 0 | `ifiles ofile` | top (curated) | ✓ | ✓ | ✓ |
| 169 | `ensmean` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 170 | `ensmedian` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 171 | `ensmin` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 172 | `enspctl` | Statistical values | n→1 | 1 | `ifiles ofile pn` | All… | ✓ | ✓ | ✓ |
| 173 | `ensrange` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 174 | `ensrkhistspace` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 175 | `ensrkhisttime` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 176 | `ensroc` | Statistical values | n→1 | 1 | `ifiles ofile nbins` | All… | ✓ | ✓ | ✓ |
| 177 | `ensskew` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 178 | `ensstd` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 179 | `ensstd1` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 180 | `enssum` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 181 | `ensvar` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 182 | `ensvar1` | Statistical values | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 183 | `eof` | EOFs | 1→2 | 1 | `ifile ofile1 ofile2 neof` | top | ✓ | ✓ | ✓ |
| 184 | `eof3d` | EOFs | 1→2 | 1 | `ifile ofile1 ofile2 neof` | top | ✓ | ✓ | ✓ |
| 185 | `eof3dspatial` | EOFs | 1→2 | 1 | `ifile ofile1 ofile2 neof` | top | ✓ | ✓ | ✓ |
| 186 | `eof3dtime` | EOFs | 1→2 | 1 | `ifile ofile1 ofile2 neof` | top | ✓ | ✓ | ✓ |
| 187 | `eofcoeff` | EOFs | 2→n | 0 | `ifile1 ifile2 obase` | top | ✓ | ✓ | ✓ |
| 188 | `eofcoeff3d` | EOFs | 2→n | 0 | `ifile1 ifile2 obase` | top | ✓ | ✓ | ✓ |
| 189 | `eofspatial` | EOFs | 1→2 | 1 | `ifile ofile1 ofile2 neof` | top | ✓ | ✓ | ✓ |
| 190 | `eoftime` | EOFs | 1→2 | 1 | `ifile ofile1 ofile2 neof` | top | ✓ | ✓ | ✓ |
| 191 | `eq` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 192 | `eqc` | Comparison | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 193 | `etccdi` | ECA indices | 3→1 | 4 | `ifile1 ifile2 ifile3 ofile n,startboot,endboot[,freq]` | All… | ✓ | ✓ | ✓ |
| 194 | `etccdi_cdd` | ECA indices | 1→1 | 3 | `ifile ofile [,R][,N][,freq]` | All… | ✓ | ✓ | ✓ |
| 195 | `etccdi_csdi` | ECA indices | 2→1 | 2 | `ifile1 ifile2 ofile [,nday][,freq]` | All… | ✓ | ✓ | ✓ |
| 196 | `etccdi_cwd` | ECA indices | 1→1 | 3 | `ifile ofile [,R][,N][,freq]` | All… | ✓ | ✓ | ✓ |
| 197 | `etccdi_fd` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | All… | ✓ | ✓ | ✓ |
| 198 | `etccdi_gsl` | ECA indices | 2→1 | 3 | `ifile1 ifile2 ofile [,nday][,T][,fland]` | All… | ✓ | ✓ | ✓ |
| 199 | `etccdi_hd` | ECA indices | 1→1 | 2 | `ifile ofile [,T1][,T2]` | All… | ✓ | ✓ | ✓ |
| 200 | `etccdi_id` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | All… | ✓ | ✓ | ✓ |
| 201 | `etccdi_r10mm` | ECA indices | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 202 | `etccdi_r1mm` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | All… | ✓ | ✓ | ✓ |
| 203 | `etccdi_r20mm` | ECA indices | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 204 | `etccdi_r95p` | ECA indices | 3→1 | 3 | `ifile1 ifile2 ifile3 ofile startboot,endboot,freq` | All… | ✓ | ✓ | ✓ |
| 205 | `etccdi_r99p` | ECA indices | 3→1 | 3 | `ifile1 ifile2 ifile3 ofile startboot,endboot,freq` | All… | ✓ | ✓ | ✓ |
| 206 | `etccdi_rx1day` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | All… | ✓ | ✓ | ✓ |
| 207 | `etccdi_rx1daymon` | ECA indices | 1→1 | 1 | `ifile ofile [,freq]` | All… | ✓ | ✓ | ✓ |
| 208 | `etccdi_rx5day` | ECA indices | 1→1 | 2 | `ifile ofile [,x][,freq]` | All… | ✓ | ✓ | ✓ |
| 209 | `etccdi_rx5daymon` | ECA indices | 1→1 | 2 | `ifile ofile [,x][,freq]` | All… | ✓ | ✓ | ✓ |
| 210 | `etccdi_sdii` | ECA indices | 1→1 | 1 | `ifile ofile [,R]` | All… | ✓ | ✓ | ✓ |
| 211 | `etccdi_su` | ECA indices | 1→1 | 2 | `ifile ofile [,T][,freq]` | All… | ✓ | ✓ | ✓ |
| 212 | `etccdi_tn10p` | ECA indices | 3→1 | 4 | `ifile1 ifile2 ifile3 ofile n,startboot,endboot[,freq]` | All… | ✓ | ✓ | ✓ |
| 213 | `etccdi_tn90p` | ECA indices | 3→1 | 4 | `ifile1 ifile2 ifile3 ofile n,startboot,endboot[,freq]` | All… | ✓ | ✓ | ✓ |
| 214 | `etccdi_tr` | ECA indices | 1→1 | 2 | `ifile ofile [,T][,freq]` | All… | ✓ | ✓ | ✓ |
| 215 | `etccdi_tx10p` | ECA indices | 3→1 | 4 | `ifile1 ifile2 ifile3 ofile n,startboot,endboot[,freq]` | All… | ✓ | ✓ | ✓ |
| 216 | `etccdi_tx90p` | ECA indices | 3→1 | 4 | `ifile1 ifile2 ifile3 ofile n,startboot,endboot[,freq]` | All… | ✓ | ✓ | ✓ |
| 217 | `etccdi_wsdi` | ECA indices | 2→1 | 2 | `ifile1 ifile2 ofile [,nday][,freq]` | All… | ✓ | ✓ | ✓ |
| 218 | `exp` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 219 | `export_e5ml` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 220 | `expr` | Arithmetic | 1→1 | 1 | `ifile ofile instr` | top (curated) | ✓ | ✓ | ✓ |
| 221 | `exprf` | Arithmetic | 1→1 | 1 | `ifile ofile filename` | All… | ✓ | ✓ | ✓ |
| 222 | `fc2gp` | Transformation | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 223 | `fc2sp` | Transformation | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 224 | `fdns` | Miscellaneous | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 225 | `filedes` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 226 | `fillmiss` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 227 | `fillmiss2` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 228 | `fldavg` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 229 | `fldcor` | Correlation | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 230 | `fldcount` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 231 | `fldcovar` | Correlation | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 232 | `fldint` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 233 | `fldkurt` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 234 | `fldmax` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 235 | `fldmean` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 236 | `fldmedian` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 237 | `fldmin` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 238 | `fldpctl` | Statistical values | 1→1 | 1 | `ifile ofile pn=<float>` | All… | ✓ | ✓ | ✓ |
| 239 | `fldrange` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 240 | `fldrms` | Statistical values | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 241 | `fldskew` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 242 | `fldstd` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 243 | `fldstd1` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 244 | `fldsum` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 245 | `fldvar` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 246 | `fldvar1` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 247 | `for` | Miscellaneous | 0→1 | 3 | `ofile start,stop[,step]` | All… | ✓ | ✓ | ✓ |
| 248 | `fourier` | Transformation | 1→1 | 1 | `ifile ofile epsilon` | All… | ✓ | ✓ | ✓ |
| 249 | `fourier2grid` | Transformation | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 250 | `ge` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 251 | `gec` | Comparison | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 252 | `genbic` | Interpolation | 1→1 | 2 | `ifile ofile grid[,map3d=true]` | top (curated) | ✓ | ✓ | ✓ |
| 253 | `genbil` | Interpolation | 1→1 | 2 | `ifile ofile grid[,map3d=true]` | top (curated) | ✓ | ✓ | ✓ |
| 254 | `gencon` | Interpolation | 1→1 | 2 | `ifile ofile grid[,map3d=true]` | top (curated) | ✓ | ✓ | ✓ |
| 255 | `gendis` | Interpolation | 1→1 | 2 | `ifile ofile grid[,map3d=true]` | top (curated) | ✓ | ✓ | ✓ |
| 256 | `gengrid` | Interpolation | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 257 | `genknn` | Interpolation | 1→1 | 6 | `ifile ofile grid=<grid>[,k=<int>][,kmin=<int>][,weighted=<select>][,gauss_scale=<float>][,extrapolate=true]` | All… | ✓ | ✓ | ✓ |
| 258 | `genlaf` | Interpolation | 1→1 | 2 | `ifile ofile grid[,map3d=true]` | All… | ✓ | ✓ | ✓ |
| 259 | `genlevelbounds` | Interpolation | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 260 | `gennn` | Interpolation | 1→1 | 2 | `ifile ofile grid[,map3d=true]` | All… | ✓ | ✓ | ✓ |
| 261 | `genycon` | Interpolation | 1→1 | 2 | `ifile ofile grid[,map3d=true]` | All… | ✓ | ✓ | ✓ |
| 262 | `genycon2test` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 263 | `gh2hl` | Miscellaneous | 1→1 | 1 | `ifile ofile levels` | All… | ✓ | ✓ | ✓ |
| 264 | `gh2hlx` | Miscellaneous | 1→1 | 1 | `ifile ofile levels` | All… | ✓ | ✓ | ✓ |
| 265 | `gheight` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 266 | `gheight_full` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 267 | `gheight_half` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 268 | `gheighthalf` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 269 | `globavg` | Statistical values | 1→1 | 2 | `ifile ofile [,weights=true][,verbose=true]` | All… | ✓ | ✓ | ✓ |
| 270 | `gmtcells` | Import/Export | 1→0 | 0 | `ifile` | top (curated) | ✓ | ✓ | ✓ |
| 271 | `gmtxyz` | Import/Export | 1→0 | 0 | `ifile` | top (curated) | ✓ | ✓ | ✓ |
| 272 | `gp2fc` | Transformation | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 273 | `gp2sp` | Transformation | 1→1 | 1 | `ifile ofile [,type]` | top (curated) | ✓ | ✓ | ✓ |
| 274 | `gp2spl` | Transformation | 1→1 | 1 | `ifile ofile [,type]` | top (curated) | ✓ | ✓ | ✓ |
| 275 | `gradsdes` | Miscellaneous | 1→0 | 1 | `ifile [,mapversion]` | All… | ✓ | ✓ | ✓ |
| 276 | `graph` | Graphics | n→1 | 7 | `ifiles obase [,device=<select>][,ymin=<float>][,ymax=<float>][,linewidth=<int>][,stat=true][,sigma=<float>][,obsv=true]` | top | ✓ | ✓ | ✓ |
| 277 | `grfill` | Graphics | 1→1 | 19 | `ifile obase [,device=<select>][,projection=<select>][,style=<select>][,min=<float>][,max=<float>][,lon_min=<float>][,lon_max=<float>][,lat_min=<float>][,lat_max=<float>][,count=<int>][,interval=<float>][,list=<string>][,RGB=true][,step_freq=<int>][,file_split=true][,colour_min=<select>][,colour_max=<select>][,colour_triad=<select>][,colour_table=<file>]` | top | ✓ | ✓ | ✓ |
| 278 | `grid2fourier` | Transformation | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 279 | `gridarea` | Miscellaneous | 1→1 | 1 | `ifile ofile [,radius=<float>]` | top (curated) | ✓ | ✓ | ✓ |
| 280 | `gridboxavg` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 281 | `gridboxkurt` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 282 | `gridboxmax` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 283 | `gridboxmean` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 284 | `gridboxmedian` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 285 | `gridboxmin` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 286 | `gridboxrange` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 287 | `gridboxskew` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 288 | `gridboxstd` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 289 | `gridboxstd1` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 290 | `gridboxsum` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 291 | `gridboxvar` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 292 | `gridboxvar1` | Statistical values | 1→1 | 2 | `ifile ofile nx,ny` | All… | ✓ | ✓ | ✓ |
| 293 | `gridcellidx` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 294 | `gridcellindex` | Information | 1→0 | 2 | `ifile [,lon=<float>][,lat=<float>]` | All… | ✓ | ✓ | ✓ |
| 295 | `griddes` | Information | 1→0 | 0 | `ifile` | top (curated) | ✓ | ✓ | ✓ |
| 296 | `griddes2` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 297 | `griddx` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 298 | `griddy` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 299 | `gridmask` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 300 | `gridweights` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 301 | `gt` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 302 | `gtc` | Comparison | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 303 | `harmonic` | Miscellaneous | 1→1 | 2 | `ifile ofile nwaves,end` | All… | ✓ | ✓ | ✓ |
| 304 | `highpass` | Miscellaneous | 1→1 | 1 | `ifile ofile fmin` | All… | ✓ | ✓ | ✓ |
| 305 | `histcount` | Miscellaneous | 1→1 | 1 | `ifile ofile bounds` | All… | ✓ | ✓ | ✓ |
| 306 | `histfreq` | Miscellaneous | 1→1 | 1 | `ifile ofile bounds` | All… | ✓ | ✓ | ✓ |
| 307 | `histmean` | Miscellaneous | 1→1 | 1 | `ifile ofile bounds` | All… | ✓ | ✓ | ✓ |
| 308 | `histsum` | Miscellaneous | 1→1 | 1 | `ifile ofile bounds` | All… | ✓ | ✓ | ✓ |
| 309 | `houravg` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 310 | `hourcount` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 311 | `hourmax` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 312 | `hourmean` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 313 | `hourmin` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 314 | `hourpctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 315 | `hourrange` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 316 | `hourstd` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 317 | `hourstd1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 318 | `hoursum` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 319 | `hourvar` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 320 | `hourvar1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 321 | `hpdegrade` | Miscellaneous | 1→1 | 3 | `ifile ofile [,nside=<int>][,order=<select>][,power=<float>]` | All… | ✓ | ✓ | ✓ |
| 322 | `hpupgrade` | Miscellaneous | 1→1 | 2 | `ifile ofile [,zoom=<int>][,order=<select>]` | All… | ✓ | ✓ | ✓ |
| 323 | `hurr` | Miscellaneous | 1→1 | 1 | `ifile ofile [,v=<float>]` | All… | ✓ | ✓ | ✓ |
| 324 | `ifnotthen` | Conditional selection | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 325 | `ifnotthenc` | Conditional selection | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 326 | `ifthen` | Conditional selection | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 327 | `ifthenc` | Conditional selection | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 328 | `ifthenelse` | Conditional selection | 3→1 | 0 | `ifile1 ifile2 ifile3 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 329 | `im` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 330 | `import_binary` | Import/Export | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 331 | `import_cmsaf` | Import/Export | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 332 | `import_e5ml` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 333 | `import_fv3grid` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 334 | `import_grads` | Import/Export | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 335 | `import_obs` | Miscellaneous | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 336 | `imtocomplex` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 337 | `info` | Information | n→0 | 0 | `ifiles` | top (curated) | ✓ | ✓ | ✓ |
| 338 | `infoc` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 339 | `infon` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 340 | `infop` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 341 | `infov` | Information | n→0 | 0 | `ifiles` | top (curated) | ✓ | ✓ | ✓ |
| 342 | `input` | Import/Export | 0→1 | 2 | `ofile grid[,zaxis]` | top (curated) | ✓ | ✓ | ✓ |
| 343 | `inputext` | Import/Export | 0→1 | 0 | `ofile` | All… | ✓ | ✓ | ✓ |
| 344 | `inputsrv` | Import/Export | 0→1 | 0 | `ofile` | All… | ✓ | ✓ | ✓ |
| 345 | `int` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 346 | `intgridbil` | Interpolation | 1→1 | 1 | `ifile ofile grid` | top (curated) | ✓ | ✓ | ✓ |
| 347 | `intgriddis` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 348 | `intgridknn` | Interpolation | 1→1 | 6 | `ifile ofile grid=<grid>[,k=<int>][,kmin=<int>][,weighted=<select>][,gauss_scale=<float>][,extrapolate=true]` | All… | ✓ | ✓ | ✓ |
| 349 | `intgridnn` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 350 | `intgridtraj` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 351 | `intlevel` | Interpolation | 1→1 | 4 | `ifile ofile level=<string>[,zdescription=<file>][,zvarname=<string>][,extrapolate=true]` | All… | ✓ | ✓ | ✓ |
| 352 | `intlevel3d` | Interpolation | 2→1 | 1 | `ifile1 ifile2 ofile tgtcoordinate` | All… | ✓ | ✓ | ✓ |
| 353 | `intlevelx` | Interpolation | 1→1 | 4 | `ifile ofile level=<string>[,zdescription=<file>][,zvarname=<string>][,extrapolate=true]` | All… | ✓ | ✓ | ✓ |
| 354 | `intlevelx3d` | Interpolation | 2→1 | 1 | `ifile1 ifile2 ofile tgtcoordinate` | All… | ✓ | ✓ | ✓ |
| 355 | `intntime` | Interpolation | 1→1 | 1 | `ifile ofile n` | top (curated) | ✓ | ✓ | ✓ |
| 356 | `inttime` | Interpolation | 1→1 | 3 | `ifile ofile date,time[,inc]` | top (curated) | ✓ | ✓ | ✓ |
| 357 | `intyear` | Interpolation | 2→n | 1 | `ifile1 ifile2 obase years` | top (curated) | ✓ | ✓ | ✓ |
| 358 | `invertlat` | Modification | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 359 | `invertlatdata` | Modification | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 360 | `invertlatdes` | Modification | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 361 | `invertlev` | Modification | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 362 | `invertlon` | Modification | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 363 | `invertlondata` | Modification | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 364 | `invertlondes` | Modification | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 365 | `isosurface` | Miscellaneous | 1→1 | 1 | `ifile ofile value` | All… | ✓ | ✓ | ✓ |
| 366 | `le` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 367 | `lec` | Comparison | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 368 | `lic` | Miscellaneous | 1→1 | 1 | `ifile ofile cpt` | All… | ✓ | ✓ | ✓ |
| 369 | `linfo` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 370 | `ln` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 371 | `log` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 372 | `log10` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 373 | `lowpass` | Miscellaneous | 1→1 | 1 | `ifile ofile fmax` | All… | ✓ | ✓ | ✓ |
| 374 | `lt` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 375 | `ltc` | Comparison | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 376 | `map` | Information | n→0 | 0 | `ifiles` | top (curated) | ✓ | ✓ | ✓ |
| 377 | `mask` | Miscellaneous | 0→1 | 1 | `ofile grid` | All… | ✓ | ✓ | ✓ |
| 378 | `maskcircle` | Modification | 1→1 | 3 | `ifile ofile lon0,lat0,r` | All… | ✓ | ✓ | ✓ |
| 379 | `maskindexbox` | Modification | 1→1 | 4 | `ifile ofile idx1,idx2,idy1,idy2` | All… | ✓ | ✓ | ✓ |
| 380 | `masklonlatbox` | Modification | 1→1 | 4 | `ifile ofile lon1,lon2,lat1,lat2` | All… | ✓ | ✓ | ✓ |
| 381 | `maskregion` | Modification | 1→1 | 1 | `ifile ofile regions` | All… | ✓ | ✓ | ✓ |
| 382 | `mastrfu` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 383 | `max` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 384 | `maxc` | Arithmetic | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 385 | `meandiff2test` | Miscellaneous | 2→1 | 2 | `ifile1 ifile2 ofile c,risk` | All… | ✓ | ✓ | ✓ |
| 386 | `meravg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 387 | `merge` | File operations | n→1 | 0 | `ifiles ofile` | top (curated) | ✓ | ✓ | ✓ |
| 388 | `mergegrid` | File operations | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 389 | `mergetime` | File operations | n→1 | 2 | `ifiles ofile [,skip_same_time=true][,names=<select>]` | top (curated) | ✓ | ✓ | ✓ |
| 390 | `merkurt` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 391 | `mermax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 392 | `mermean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 393 | `mermedian` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 394 | `mermin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 395 | `merpctl` | Statistical values | 1→1 | 1 | `ifile ofile pn` | All… | ✓ | ✓ | ✓ |
| 396 | `merrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 397 | `merskew` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 398 | `merstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 399 | `merstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 400 | `mersum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 401 | `mervar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 402 | `mervar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 403 | `min` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 404 | `minc` | Arithmetic | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 405 | `ml2hl` | Interpolation | 1→1 | 1 | `ifile ofile hlevels` | top (curated) | ✓ | ✓ | ✓ |
| 406 | `ml2hlx` | Interpolation | 1→1 | 1 | `ifile ofile hlevels` | All… | ✓ | ✓ | ✓ |
| 407 | `ml2pl` | Interpolation | 1→1 | 1 | `ifile ofile plevels` | top (curated) | ✓ | ✓ | ✓ |
| 408 | `ml2plx` | Interpolation | 1→1 | 1 | `ifile ofile plevels` | All… | ✓ | ✓ | ✓ |
| 409 | `mod` | Arithmetic | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 410 | `monadd` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 411 | `monavg` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 412 | `moncount` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 413 | `mondiv` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 414 | `monmax` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 415 | `monmean` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 416 | `monmin` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 417 | `monmul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 418 | `monpctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 419 | `monrange` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 420 | `monstd` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 421 | `monstd1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 422 | `monsub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 423 | `monsum` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 424 | `monvar` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 425 | `monvar1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 426 | `mrotuv` | Miscellaneous | 1→2 | 0 | `ifile ofile1 ofile2` | All… | ✓ | ✓ | ✓ |
| 427 | `mrotuvb` | Miscellaneous | 2→1 | 1 | `ifile1 ifile2 ofile [,noint]` | All… | ✓ | ✓ | ✓ |
| 428 | `mul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 429 | `mulc` | Arithmetic | 1→1 | 1 | `ifile ofile c` | top (curated) | ✓ | ✓ | ✓ |
| 430 | `mulcoslat` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 431 | `muldoy` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 432 | `muldpm` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 433 | `muldpy` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 434 | `ncode` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 435 | `ncopy` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 436 | `ndate` | Information | 1→0 | 0 | `ifile` | top (curated) | ✓ | ✓ | ✓ |
| 437 | `ne` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 438 | `nec` | Comparison | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 439 | `ngridpoints` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 440 | `ngrids` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 441 | `nint` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 442 | `nlevel` | Information | 1→0 | 0 | `ifile` | top (curated) | ✓ | ✓ | ✓ |
| 443 | `nmon` | Information | 1→0 | 0 | `ifile` | top (curated) | ✓ | ✓ | ✓ |
| 444 | `not` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 445 | `npar` | Information | 1→0 | 0 | `ifile` | top (curated) | ✓ | ✓ | ✓ |
| 446 | `ntime` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 447 | `nvar` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 448 | `nyear` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 449 | `output` | Import/Export | n→0 | 0 | `ifiles` | top (curated) | ✓ | ✓ | ✓ |
| 450 | `outputarr` | Import/Export | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 451 | `outputbounds` | Import/Export | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 452 | `outputboundscpt` | Import/Export | 1→0 | 1 | `ifile cpt` | All… | ✓ | ✓ | ✓ |
| 453 | `outputcenter` | Import/Export | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 454 | `outputcenter2` | Import/Export | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 455 | `outputcentercpt` | Import/Export | 1→0 | 1 | `ifile cpt` | All… | ✓ | ✓ | ✓ |
| 456 | `outputext` | Import/Export | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 457 | `outputf` | Import/Export | n→0 | 2 | `ifiles [,format][,nelem]` | top (curated) | ✓ | ✓ | ✓ |
| 458 | `outputfld` | Import/Export | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 459 | `outputint` | Import/Export | n→0 | 0 | `ifiles` | top (curated) | ✓ | ✓ | ✓ |
| 460 | `outputkey` | Import/Export | n→0 | 1 | `ifiles keynames` | All… | ✓ | ✓ | ✓ |
| 461 | `outputkml` | Import/Export | 1→0 | 1 | `ifile cpt` | All… | ✓ | ✓ | ✓ |
| 462 | `outputsrv` | Import/Export | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 463 | `outputtab` | Import/Export | n→0 | 1 | `ifiles keynames` | top (curated) | ✓ | ✓ | ✓ |
| 464 | `outputtri` | Import/Export | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 465 | `outputts` | Import/Export | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 466 | `outputvector` | Import/Export | 1→0 | 1 | `ifile increment` | All… | ✓ | ✓ | ✓ |
| 467 | `outputvrml` | Import/Export | 1→0 | 1 | `ifile cpt` | All… | ✓ | ✓ | ✓ |
| 468 | `outputxyz` | Import/Export | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 469 | `pack` | File operations | 1→1 | 2 | `ifile ofile [,printparam=true][,filename=<file>]` | All… | ✓ | ✓ | ✓ |
| 470 | `pardup` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 471 | `parmul` | Miscellaneous | 1→1 | 1 | `ifile ofile number` | All… | ✓ | ✓ | ✓ |
| 472 | `partab` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 473 | `partab2` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 474 | `pinfo` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 475 | `pinfov` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 476 | `pow` | Arithmetic | 1→1 | 1 | `ifile ofile value` | All… | ✓ | ✓ | ✓ |
| 477 | `pressure` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 478 | `pressure_full` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 479 | `pressure_half` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 480 | `projuvLatLon` | Miscellaneous | 1→1 | 1 | `ifile ofile pairs` | All… | ✓ | ✓ | ✓ |
| 481 | `rand` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 482 | `random` | Miscellaneous | 0→1 | 2 | `ofile grid[,seed]` | top (curated) | ✓ | ✓ | ✓ |
| 483 | `re` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 484 | `reci` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 485 | `recttocomplex` | Miscellaneous | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 486 | `reducegrid` | Conditional selection | 1→1 | 2 | `ifile ofile mask[,limitCoordsOutput]` | top | ✓ | ✓ | ✓ |
| 487 | `regres` | Regression | 1→1 | 1 | `ifile ofile [,equal=true]` | top (curated) | ✓ | ✓ | ✓ |
| 488 | `remap` | Interpolation | 1→1 | 2 | `ifile ofile grid,weights` | All… | ✓ | ✓ | ✓ |
| 489 | `remapavg` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 490 | `remapavgtest` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 491 | `remapbic` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 492 | `remapbil` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 493 | `remapcon` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 494 | `remapdis` | Interpolation | 1→1 | 2 | `ifile ofile grid[,k]` | All… | ✓ | ✓ | ✓ |
| 495 | `remapeta` | Interpolation | 1→1 | 2 | `ifile ofile vct[,oro]` | All… | ✓ | ✓ | ✓ |
| 496 | `remapeta_s` | Interpolation | 1→1 | 1 | `ifile ofile vct` | All… | ✓ | ✓ | ✓ |
| 497 | `remapeta_z` | Interpolation | 1→1 | 1 | `ifile ofile vct` | All… | ✓ | ✓ | ✓ |
| 498 | `remapknn` | Interpolation | 1→1 | 6 | `ifile ofile grid=<grid>[,k=<int>][,kmin=<int>][,weighted=<select>][,gauss_scale=<float>][,extrapolate=true]` | All… | ✓ | ✓ | ✓ |
| 499 | `remapkurt` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 500 | `remaplaf` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 501 | `remapmax` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 502 | `remapmean` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 503 | `remapmedian` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 504 | `remapmin` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 505 | `remapnn` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 506 | `remaprange` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 507 | `remapskew` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 508 | `remapstd` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 509 | `remapstd1` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 510 | `remapsum` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 511 | `remapvar` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 512 | `remapvar1` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 513 | `remapycon` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 514 | `remapycon2test` | Interpolation | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 515 | `replace` | File operations | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 516 | `retocomplex` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 517 | `rhopot` | Miscellaneous | 1→1 | 1 | `ifile ofile [,pressure]` | All… | ✓ | ✓ | ✓ |
| 518 | `rotuvN` | Miscellaneous | 1→1 | 1 | `ifile ofile pairs` | All… | ✓ | ✓ | ✓ |
| 519 | `rotuvNorth` | Miscellaneous | 1→1 | 1 | `ifile ofile pairs` | All… | ✓ | ✓ | ✓ |
| 520 | `rotuvb` | Miscellaneous | 1→1 | 1 | `ifile ofile pairs` | All… | ✓ | ✓ | ✓ |
| 521 | `runavg` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 522 | `runmax` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 523 | `runmean` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 524 | `runmin` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 525 | `runpctl` | Statistical values | 1→1 | 2 | `ifile ofile pn,nts` | All… | ✓ | ✓ | ✓ |
| 526 | `runrange` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 527 | `runstd` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 528 | `runstd1` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 529 | `runsum` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 530 | `runvar` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 531 | `runvar1` | Statistical values | 1→1 | 1 | `ifile ofile nts` | All… | ✓ | ✓ | ✓ |
| 532 | `samplegrid` | Miscellaneous | 1→1 | 1 | `ifile ofile factor` | All… | ✓ | ✓ | ✓ |
| 533 | `samplegridicon` | Miscellaneous | 1→2 | 2 | `ifile ofile1 ofile2 gridfile,factor` | All… | ✓ | ✓ | ✓ |
| 534 | `sealevelpressure` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 535 | `seasavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 536 | `seascount` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 537 | `seasmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 538 | `seasmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 539 | `seasmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 540 | `seasmonavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 541 | `seasmonmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 542 | `seaspctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 543 | `seasrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 544 | `seasstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 545 | `seasstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 546 | `seassum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 547 | `seasvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 548 | `seasvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 549 | `seinfo` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 550 | `seinfoc` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 551 | `seinfon` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 552 | `seinfop` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 553 | `selcircle` | Selection | 1→1 | 1 | `ifile ofile circle` | All… | ✓ | ✓ | ✓ |
| 554 | `selcode` | Selection | 1→1 | 1 | `ifile ofile codes` | top (curated) | ✓ | ✓ | ✓ |
| 555 | `seldate` | Selection | 1→1 | 2 | `ifile ofile date1[,date2]` | top (curated) | ✓ | ✓ | ✓ |
| 556 | `selday` | Selection | 1→1 | 1 | `ifile ofile days` | top (curated) | ✓ | ✓ | ✓ |
| 557 | `select` | Selection | n→1 | 1 | `ifiles ofile selection` | All… | ✓ | ✓ | ✓ |
| 558 | `selgrid` | Selection | 1→1 | 1 | `ifile ofile grids` | top (curated) | ✓ | ✓ | ✓ |
| 559 | `selgridcell` | Selection | 1→1 | 1 | `ifile ofile cells` | All… | ✓ | ✓ | ✓ |
| 560 | `selgridname` | Selection | 1→1 | 1 | `ifile ofile gridnames` | top (curated) | ✓ | ✓ | ✓ |
| 561 | `selhour` | Selection | 1→1 | 1 | `ifile ofile hours` | top (curated) | ✓ | ✓ | ✓ |
| 562 | `selindexbox` | Selection | 1→1 | 4 | `ifile ofile idx1,idx2,idy1,idy2` | top (curated) | ✓ | ✓ | ✓ |
| 563 | `sellevel` | Selection | 1→1 | 1 | `ifile ofile levels` | top (curated) | ✓ | ✓ | ✓ |
| 564 | `sellevidx` | Selection | 1→1 | 1 | `ifile ofile levidx` | All… | ✓ | ✓ | ✓ |
| 565 | `sellonlatbox` | Selection | 1→1 | 4 | `ifile ofile lon1,lon2,lat1,lat2` | All… | ✓ | ✓ | ✓ |
| 566 | `selltype` | Selection | 1→1 | 1 | `ifile ofile ltypes` | All… | ✓ | ✓ | ✓ |
| 567 | `selmon` | Selection | 1→1 | 1 | `ifile ofile months` | All… | ✓ | ✓ | ✓ |
| 568 | `selmonth` | Selection | 1→1 | 1 | `ifile ofile months` | All… | ✓ | ✓ | ✓ |
| 569 | `selmulti` | Selection | 1→1 | 1 | `ifile ofile params` | All… | ✓ | ✓ | ✓ |
| 570 | `selname` | Selection | 1→1 | 1 | `ifile ofile vars` | All… | ✓ | ✓ | ✓ |
| 571 | `seloperator` | Selection | 1→1 | 1 | `ifile ofile params` | All… | ✓ | ✓ | ✓ |
| 572 | `selparam` | Selection | 1→1 | 1 | `ifile ofile params` | All… | ✓ | ✓ | ✓ |
| 573 | `selrec` | Selection | 1→1 | 1 | `ifile ofile records` | All… | ✓ | ✓ | ✓ |
| 574 | `selregion` | Selection | 1→1 | 1 | `ifile ofile regions` | All… | ✓ | ✓ | ✓ |
| 575 | `selseas` | Selection | 1→1 | 1 | `ifile ofile seasons` | All… | ✓ | ✓ | ✓ |
| 576 | `selseason` | Selection | 1→1 | 1 | `ifile ofile seasons` | All… | ✓ | ✓ | ✓ |
| 577 | `selsmon` | Selection | 1→1 | 3 | `ifile ofile month[,nts1][,nts2]` | All… | ✓ | ✓ | ✓ |
| 578 | `selstdname` | Selection | 1→1 | 1 | `ifile ofile stdnames` | All… | ✓ | ✓ | ✓ |
| 579 | `seltabnum` | Selection | 1→1 | 1 | `ifile ofile tabnums` | All… | ✓ | ✓ | ✓ |
| 580 | `seltime` | Selection | 1→1 | 1 | `ifile ofile times` | All… | ✓ | ✓ | ✓ |
| 581 | `seltimeidx` | Selection | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 582 | `seltimestep` | Selection | 1→1 | 1 | `ifile ofile timesteps` | All… | ✓ | ✓ | ✓ |
| 583 | `selvar` | Selection | 1→1 | 1 | `ifile ofile vars` | All… | ✓ | ✓ | ✓ |
| 584 | `selyear` | Selection | 1→1 | 1 | `ifile ofile years` | All… | ✓ | ✓ | ✓ |
| 585 | `selyearidx` | Selection | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 586 | `selzaxis` | Selection | 1→1 | 1 | `ifile ofile zaxes` | All… | ✓ | ✓ | ✓ |
| 587 | `selzaxisname` | Selection | 1→1 | 1 | `ifile ofile zaxisnames` | All… | ✓ | ✓ | ✓ |
| 588 | `seq` | Miscellaneous | 0→1 | 3 | `ofile start,end[,inc]` | All… | ✓ | ✓ | ✓ |
| 589 | `setattribute` | Modification | 1→1 | 1 | `ifile ofile attrs` | All… | ✓ | ✓ | ✓ |
| 590 | `setcalendar` | Modification | 1→1 | 1 | `ifile ofile calendar` | All… | ✓ | ✓ | ✓ |
| 591 | `setchunkspec` | File operations | 1→1 | 1 | `ifile ofile filename=<file>` | All… | ✓ | ✓ | ✓ |
| 592 | `setcindexbox` | Modification | 1→1 | 5 | `ifile ofile c,idx1,idx2,idy1,idy2` | All… | ✓ | ✓ | ✓ |
| 593 | `setclonlatbox` | Modification | 1→1 | 5 | `ifile ofile c,lon1,lon2,lat1,lat2` | All… | ✓ | ✓ | ✓ |
| 594 | `setcode` | Modification | 1→1 | 1 | `ifile ofile code` | All… | ✓ | ✓ | ✓ |
| 595 | `setcodetab` | Modification | 1→1 | 1 | `ifile ofile table` | All… | ✓ | ✓ | ✓ |
| 596 | `setctomiss` | Modification | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 597 | `setdate` | Modification | 1→1 | 1 | `ifile ofile date` | All… | ✓ | ✓ | ✓ |
| 598 | `setday` | Modification | 1→1 | 1 | `ifile ofile day` | All… | ✓ | ✓ | ✓ |
| 599 | `setfilter` | File operations | 1→1 | 1 | `ifile ofile filename=<file>` | All… | ✓ | ✓ | ✓ |
| 600 | `setgrid` | Modification | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 601 | `setgridarea` | Modification | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 602 | `setgridcell` | Modification | 1→1 | 1 | `ifile ofile cells` | All… | ✓ | ✓ | ✓ |
| 603 | `setgridmask` | Modification | 1→1 | 1 | `ifile ofile grid` | All… | ✓ | ✓ | ✓ |
| 604 | `setgridnumber` | Modification | 1→1 | 1 | `ifile ofile number` | All… | ✓ | ✓ | ✓ |
| 605 | `setgridtype` | Modification | 1→1 | 1 | `ifile ofile gridtype` | All… | ✓ | ✓ | ✓ |
| 606 | `setgriduri` | Modification | 1→1 | 1 | `ifile ofile uri` | All… | ✓ | ✓ | ✓ |
| 607 | `sethalo` | Miscellaneous | 1→1 | 5 | `ifile ofile [,east=<int>][,west=<int>][,south=<int>][,north=<int>][,value=<float>]` | top (curated) | ✓ | ✓ | ✓ |
| 608 | `setlevel` | Modification | 1→1 | 1 | `ifile ofile level` | All… | ✓ | ✓ | ✓ |
| 609 | `setltype` | Modification | 1→1 | 1 | `ifile ofile ltype` | All… | ✓ | ✓ | ✓ |
| 610 | `setmaxsteps` | Modification | 1→1 | 1 | `ifile ofile nsteps` | All… | ✓ | ✓ | ✓ |
| 611 | `setmiss` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 612 | `setmisstoc` | Modification | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 613 | `setmisstodis` | Modification | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 614 | `setmisstonn` | Modification | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 615 | `setmissval` | Modification | 1→1 | 1 | `ifile ofile miss` | All… | ✓ | ✓ | ✓ |
| 616 | `setmon` | Modification | 1→1 | 1 | `ifile ofile month` | All… | ✓ | ✓ | ✓ |
| 617 | `setname` | Modification | 1→1 | 1 | `ifile ofile name` | All… | ✓ | ✓ | ✓ |
| 618 | `setparam` | Modification | 1→1 | 1 | `ifile ofile param` | All… | ✓ | ✓ | ✓ |
| 619 | `setpartab` | Modification | 1→1 | 1 | `ifile ofile table` | All… | ✓ | ✓ | ✓ |
| 620 | `setpartabc` | Modification | 1→1 | 1 | `ifile ofile table` | All… | ✓ | ✓ | ✓ |
| 621 | `setpartabn` | Modification | 1→1 | 1 | `ifile ofile table` | All… | ✓ | ✓ | ✓ |
| 622 | `setpartabp` | Modification | 1→1 | 1 | `ifile ofile table` | All… | ✓ | ✓ | ✓ |
| 623 | `setpartabv` | Modification | 1→1 | 1 | `ifile ofile table` | All… | ✓ | ✓ | ✓ |
| 624 | `setprojparams` | Modification | 1→1 | 1 | `ifile ofile params` | All… | ✓ | ✓ | ✓ |
| 625 | `setrcaname` | Modification | 1→1 | 1 | `ifile ofile rcafile` | All… | ✓ | ✓ | ✓ |
| 626 | `setreftime` | Modification | 1→1 | 3 | `ifile ofile date,time[,units]` | All… | ✓ | ✓ | ✓ |
| 627 | `setrtoc` | Miscellaneous | 1→1 | 3 | `ifile ofile rmin,rmax,c` | top (curated) | ✓ | ✓ | ✓ |
| 628 | `setrtoc2` | Miscellaneous | 1→1 | 4 | `ifile ofile rmin,rmax,c,c2` | All… | ✓ | ✓ | ✓ |
| 629 | `setrtomiss` | Modification | 1→1 | 2 | `ifile ofile rmin,rmax` | All… | ✓ | ✓ | ✓ |
| 630 | `setstdname` | Modification | 1→1 | 1 | `ifile ofile stdname` | All… | ✓ | ✓ | ✓ |
| 631 | `settabnum` | Modification | 1→1 | 1 | `ifile ofile tabnum` | All… | ✓ | ✓ | ✓ |
| 632 | `settaxis` | Modification | 1→1 | 3 | `ifile ofile date,time[,inc]` | All… | ✓ | ✓ | ✓ |
| 633 | `settbounds` | Modification | 1→1 | 1 | `ifile ofile frequency` | All… | ✓ | ✓ | ✓ |
| 634 | `settime` | Modification | 1→1 | 1 | `ifile ofile time` | All… | ✓ | ✓ | ✓ |
| 635 | `settunits` | Modification | 1→1 | 1 | `ifile ofile units` | All… | ✓ | ✓ | ✓ |
| 636 | `setunit` | Modification | 1→1 | 1 | `ifile ofile unit` | All… | ✓ | ✓ | ✓ |
| 637 | `setvals` | Miscellaneous | 1→1 | 1 | `ifile ofile pairs` | top (curated) | ✓ | ✓ | ✓ |
| 638 | `setvar` | Modification | 1→1 | 1 | `ifile ofile name` | All… | ✓ | ✓ | ✓ |
| 639 | `setvrange` | Modification | 1→1 | 2 | `ifile ofile rmin,rmax` | All… | ✓ | ✓ | ✓ |
| 640 | `setyear` | Modification | 1→1 | 1 | `ifile ofile year` | All… | ✓ | ✓ | ✓ |
| 641 | `setzaxis` | Modification | 1→1 | 1 | `ifile ofile zaxis` | All… | ✓ | ✓ | ✓ |
| 642 | `shaded` | Graphics | 1→1 | 19 | `ifile obase [,device=<select>][,projection=<select>][,style=<select>][,min=<float>][,max=<float>][,lon_min=<float>][,lon_max=<float>][,lat_min=<float>][,lat_max=<float>][,count=<int>][,interval=<float>][,list=<string>][,RGB=true][,step_freq=<int>][,file_split=true][,colour_min=<select>][,colour_max=<select>][,colour_triad=<select>][,colour_table=<file>]` | top | ✓ | ✓ | ✓ |
| 643 | `shifttime` | Modification | 1→1 | 1 | `ifile ofile sval` | All… | ✓ | ✓ | ✓ |
| 644 | `shiftx` | Modification | 1→1 | 1 | `ifile ofile [,nshift]` | All… | ✓ | ✓ | ✓ |
| 645 | `shifty` | Modification | 1→1 | 1 | `ifile ofile [,nshift]` | All… | ✓ | ✓ | ✓ |
| 646 | `showattribute` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 647 | `showattsvar` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 648 | `showchunkspec` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 649 | `showcode` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 650 | `showdate` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 651 | `showfilter` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 652 | `showformat` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 653 | `showgrid` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 654 | `showhistory` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 655 | `showlevel` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 656 | `showltype` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 657 | `showmon` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 658 | `showname` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 659 | `showparam` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 660 | `showstdname` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 661 | `showtime` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 662 | `showtimestamp` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 663 | `showunit` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 664 | `showvar` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 665 | `showyear` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 666 | `sin` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 667 | `sincos` | Miscellaneous | 0→1 | 0 | `ofile` | All… | ✓ | ✓ | ✓ |
| 668 | `sinfo` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 669 | `sinfoc` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 670 | `sinfon` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 671 | `sinfop` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 672 | `sinfov` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 673 | `smooth` | Miscellaneous | 1→1 | 6 | `ifile ofile [,nsmooth=<int>][,radius=<string>][,maxpoints=<int>][,weighted=<select>][,weight0=<float>][,weightR=<float>]` | top (curated) | ✓ | ✓ | ✓ |
| 674 | `smooth9` | Miscellaneous | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 675 | `sortcode` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 676 | `sortlevel` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 677 | `sortname` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 678 | `sortparam` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 679 | `sorttaxis` | Miscellaneous | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 680 | `sorttimestamp` | Miscellaneous | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 681 | `sortvar` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 682 | `sp2fc` | Transformation | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 683 | `sp2gp` | Transformation | 1→1 | 1 | `ifile ofile [,type]` | top (curated) | ✓ | ✓ | ✓ |
| 684 | `sp2gpl` | Transformation | 1→1 | 1 | `ifile ofile [,type]` | top (curated) | ✓ | ✓ | ✓ |
| 685 | `sp2sp` | Transformation | 1→1 | 1 | `ifile ofile trunc` | top (curated) | ✓ | ✓ | ✓ |
| 686 | `spartab` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 687 | `spcut` | Transformation | 1→1 | 1 | `ifile ofile wnums` | top (curated) | ✓ | ✓ | ✓ |
| 688 | `specinfo` | Information | 0→0 | 0 | `` | All… | ✓ | ✓ | ✓ |
| 689 | `spectrum` | Miscellaneous | 1→1 | 4 | `ifile ofile detrend,seglen,nseg,window` | All… | ✓ | ✓ | ✓ |
| 690 | `splitcode` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 691 | `splitdate` | File operations | 1→n | 0 | `ifile obase` | All… | ✓ | ✓ | ✓ |
| 692 | `splitdatetime` | File operations | 1→n | 0 | `ifile obase` | All… | ✓ | ✓ | ✓ |
| 693 | `splitday` | File operations | 1→n | 0 | `ifile obase` | All… | ✓ | ✓ | ✓ |
| 694 | `splitensemble` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 695 | `splitgrid` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 696 | `splithour` | File operations | 1→n | 0 | `ifile obase` | All… | ✓ | ✓ | ✓ |
| 697 | `splitlevel` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 698 | `splitmon` | File operations | 1→n | 1 | `ifile obase [,format]` | top (curated) | ✓ | ✓ | ✓ |
| 699 | `splitname` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | top (curated) | ✓ | ✓ | ✓ |
| 700 | `splitparam` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 701 | `splitrec` | File operations | 1→n | 0 | `ifile obase` | All… | ✓ | ✓ | ✓ |
| 702 | `splitseas` | File operations | 1→n | 0 | `ifile obase` | All… | ✓ | ✓ | ✓ |
| 703 | `splitsel` | File operations | 1→n | 3 | `ifile obase nsets[,noffset][,nskip]` | top (curated) | ✓ | ✓ | ✓ |
| 704 | `splittabnum` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 705 | `splitvar` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 706 | `splityear` | File operations | 1→n | 0 | `ifile obase` | top (curated) | ✓ | ✓ | ✓ |
| 707 | `splityearmon` | File operations | 1→n | 0 | `ifile obase` | All… | ✓ | ✓ | ✓ |
| 708 | `splitzaxis` | File operations | 1→n | 2 | `ifile obase [,swap][,uuid=<string>]` | All… | ✓ | ✓ | ✓ |
| 709 | `sqr` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 710 | `sqrt` | Arithmetic | 1→1 | 0 | `ifile ofile` | top (curated) | ✓ | ✓ | ✓ |
| 711 | `stdatm` | Miscellaneous | 0→1 | 1 | `ofile levels` | All… | ✓ | ✓ | ✓ |
| 712 | `strbre` | Miscellaneous | 1→1 | 1 | `ifile ofile [,v=<float>]` | All… | ✓ | ✓ | ✓ |
| 713 | `stream` | Graphics | 1→1 | 5 | `ifile obase [,device=<select>][,projection=<select>][,thin_fac=<float>][,unit_vec=<float>][,step_freq=<int>]` | top | ✓ | ✓ | ✓ |
| 714 | `strgal` | Miscellaneous | 1→1 | 1 | `ifile ofile [,v=<float>]` | All… | ✓ | ✓ | ✓ |
| 715 | `strwin` | Miscellaneous | 1→1 | 1 | `ifile ofile [,v]` | All… | ✓ | ✓ | ✓ |
| 716 | `sub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 717 | `subc` | Arithmetic | 1→1 | 1 | `ifile ofile c` | All… | ✓ | ✓ | ✓ |
| 718 | `subgrid` | Miscellaneous | 1→1 | 4 | `ifile ofile i0,i1,j0,j1` | All… | ✓ | ✓ | ✓ |
| 719 | `subtrend` | Regression | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile [,equal=true]` | top (curated) | ✓ | ✓ | ✓ |
| 720 | `symmetrize` | Miscellaneous | 1→1 | 2 | `ifile ofile [,lat=<select>][,grid=<grid>]` | All… | ✓ | ✓ | ✓ |
| 721 | `szip` | File operations | n→1 | 0 | `ifiles ofile` | All… | ✓ | ✓ | ✓ |
| 722 | `tan` | Arithmetic | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 723 | `tee` | File operations | 1→1 | 1 | `ifile ofile outfile2` | top (curated) | ✓ | ✓ | ✓ |
| 724 | `temp` | Miscellaneous | 0→1 | 0 | `ofile` | All… | ✓ | ✓ | ✓ |
| 725 | `testcellsearch` | Information | 0→0 | 2 | `sgrid,tgrid` | All… | ✓ | ✓ | ✓ |
| 726 | `testfield` | Miscellaneous | 0→1 | 1 | `ofile grid` | All… | ✓ | ✓ | ✓ |
| 727 | `testpointsearch` | Information | 0→0 | 2 | `sgrid,tgrid` | All… | ✓ | ✓ | ✓ |
| 728 | `thinout` | Miscellaneous | 1→1 | 2 | `ifile ofile xinc,yinc` | All… | ✓ | ✓ | ✓ |
| 729 | `timavg` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 730 | `timcor` | Correlation | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 731 | `timcount` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 732 | `timcovar` | Correlation | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 733 | `timcumsum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 734 | `timederivative` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 735 | `timfillmiss` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 736 | `timmax` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 737 | `timmaxidx` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 738 | `timmean` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 739 | `timmin` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 740 | `timminidx` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 741 | `timpctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 742 | `timrange` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 743 | `timrmsd` | Statistical values | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 744 | `timselavg` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 745 | `timselmax` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 746 | `timselmean` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 747 | `timselmin` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 748 | `timselpctl` | Statistical values | 3→1 | 4 | `ifile1 ifile2 ifile3 ofile pn,nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 749 | `timselrange` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 750 | `timselstd` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 751 | `timselstd1` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 752 | `timselsum` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 753 | `timselvar` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 754 | `timselvar1` | Statistical values | 1→1 | 3 | `ifile ofile nsets[,noffset][,nskip]` | All… | ✓ | ✓ | ✓ |
| 755 | `timsort` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 756 | `timstd` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 757 | `timstd1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 758 | `timsum` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 759 | `timvar` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 760 | `timvar1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 761 | `timyearavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 762 | `timyearmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 763 | `tinfo` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 764 | `topo` | Miscellaneous | 0→1 | 1 | `ofile [,grid]` | top (curated) | ✓ | ✓ | ✓ |
| 765 | `topvalue` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 766 | `tpnhalo` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 767 | `transxy` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 768 | `trend` | Regression | 1→2 | 1 | `ifile ofile1 ofile2 [,equal=true]` | top (curated) | ✓ | ✓ | ✓ |
| 769 | `tstepcount` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 770 | `unpack` | File operations | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 771 | `unsetgridmask` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 772 | `usegridnumber` | Miscellaneous | 1→1 | 1 | `ifile ofile number` | All… | ✓ | ✓ | ✓ |
| 773 | `uv2dv` | Transformation | 1→1 | 1 | `ifile ofile [,gridtype]` | top (curated) | ✓ | ✓ | ✓ |
| 774 | `uv2dv_cfd` | Miscellaneous | 1→1 | 4 | `ifile ofile [,u=<string>][,v=<string>][,boundOpt=<select>][,outMode=<select>]` | All… | ✓ | ✓ | ✓ |
| 775 | `uv2dvl` | Transformation | 1→1 | 1 | `ifile ofile [,gridtype]` | All… | ✓ | ✓ | ✓ |
| 776 | `uv2vr_cfd` | Miscellaneous | 1→1 | 4 | `ifile ofile [,u=<string>][,v=<string>][,boundOpt=<select>][,outMode=<select>]` | All… | ✓ | ✓ | ✓ |
| 777 | `uvDestag` | Miscellaneous | 1→1 | 2 | `ifile ofile pairs[,offsets]` | All… | ✓ | ✓ | ✓ |
| 778 | `vardes` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 779 | `varquot2test` | Miscellaneous | 2→1 | 2 | `ifile1 ifile2 ofile c,risk` | All… | ✓ | ✓ | ✓ |
| 780 | `varrms` | Miscellaneous | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 781 | `varsavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 782 | `varskurt` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 783 | `varsmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 784 | `varsmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 785 | `varsmedian` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 786 | `varsmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 787 | `varspctl` | Statistical values | 1→1 | 1 | `ifile ofile pn` | All… | ✓ | ✓ | ✓ |
| 788 | `varsrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 789 | `varsskew` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 790 | `varsstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 791 | `varsstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 792 | `varssum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 793 | `varsvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 794 | `varsvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 795 | `vct` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 796 | `vct2` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 797 | `vector` | Graphics | 1→1 | 5 | `ifile obase [,device=<select>][,projection=<select>][,thin_fac=<float>][,unit_vec=<float>][,step_freq=<int>]` | top | ✓ | ✓ | ✓ |
| 798 | `verifygrid` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 799 | `verifyweights` | Information | 0→0 | 1 | `weights` | All… | ✓ | ✓ | ✓ |
| 800 | `vertavg` | Statistical values | 1→1 | 1 | `ifile ofile [,weights=true]` | All… | ✓ | ✓ | ✓ |
| 801 | `vertcum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 802 | `vertcumhl` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 803 | `vertfillmiss` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 804 | `vertint` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 805 | `vertmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 806 | `vertmean` | Statistical values | 1→1 | 1 | `ifile ofile [,weights=true]` | All… | ✓ | ✓ | ✓ |
| 807 | `vertmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 808 | `vertrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 809 | `vertstd` | Statistical values | 1→1 | 1 | `ifile ofile [,weights=true]` | All… | ✓ | ✓ | ✓ |
| 810 | `vertstd1` | Statistical values | 1→1 | 1 | `ifile ofile [,weights=true]` | All… | ✓ | ✓ | ✓ |
| 811 | `vertsum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 812 | `vertvar` | Statistical values | 1→1 | 1 | `ifile ofile [,weights=true]` | All… | ✓ | ✓ | ✓ |
| 813 | `vertvar1` | Statistical values | 1→1 | 1 | `ifile ofile [,weights=true]` | All… | ✓ | ✓ | ✓ |
| 814 | `vertwind` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 815 | `vinfo` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 816 | `vlist` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 817 | `wct` | Miscellaneous | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 818 | `writegrid` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 819 | `writerandom` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 820 | `writeremapscrip` | Information | 0→0 | 2 | `weights,scrip` | All… | ✓ | ✓ | ✓ |
| 821 | `xsinfo` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 822 | `xsinfoc` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 823 | `xsinfon` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 824 | `xsinfop` | Information | n→0 | 0 | `ifiles` | All… | ✓ | ✓ | ✓ |
| 825 | `ydayadd` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 826 | `ydayavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 827 | `ydaydiv` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 828 | `ydaymax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 829 | `ydaymean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 830 | `ydaymin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 831 | `ydaymul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 832 | `ydaypctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 833 | `ydayrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 834 | `ydaystd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 835 | `ydaystd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 836 | `ydaysub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 837 | `ydaysum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 838 | `ydayvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 839 | `ydayvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 840 | `ydrunavg` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 841 | `ydrunmax` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 842 | `ydrunmean` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 843 | `ydrunmin` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 844 | `ydrunpctl` | Statistical values | 3→1 | 4 | `ifile1 ifile2 ifile3 ofile pn,nts[,rm=<select>][,pm=<select>]` | All… | ✓ | ✓ | ✓ |
| 845 | `ydrunstd` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 846 | `ydrunstd1` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 847 | `ydrunsum` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 848 | `ydrunvar` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 849 | `ydrunvar1` | Statistical values | 1→1 | 2 | `ifile ofile nts[,rm=<select>]` | All… | ✓ | ✓ | ✓ |
| 850 | `yearadd` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 851 | `yearavg` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 852 | `yearcount` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 853 | `yeardiv` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 854 | `yearmax` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 855 | `yearmaxidx` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 856 | `yearmean` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 857 | `yearmin` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 858 | `yearminidx` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 859 | `yearmonavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 860 | `yearmonmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 861 | `yearmul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 862 | `yearpctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 863 | `yearrange` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 864 | `yearstd` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 865 | `yearstd1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 866 | `yearsub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 867 | `yearsum` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 868 | `yearvar` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 869 | `yearvar1` | Statistical values | 1→1 | 1 | `ifile ofile [,complete_only=true]` | All… | ✓ | ✓ | ✓ |
| 870 | `yhouradd` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 871 | `yhouravg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 872 | `yhourdiv` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 873 | `yhourmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 874 | `yhourmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 875 | `yhourmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 876 | `yhourmul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 877 | `yhourrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 878 | `yhourstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 879 | `yhourstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 880 | `yhoursub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 881 | `yhoursum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 882 | `yhourvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 883 | `yhourvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 884 | `ymonadd` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 885 | `ymonavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 886 | `ymondiv` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 887 | `ymoneq` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 888 | `ymonge` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 889 | `ymongt` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 890 | `ymonle` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 891 | `ymonlt` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 892 | `ymonmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 893 | `ymonmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 894 | `ymonmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 895 | `ymonmul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 896 | `ymonne` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 897 | `ymonpctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 898 | `ymonrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 899 | `ymonstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 900 | `ymonstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 901 | `ymonsub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | top (curated) | ✓ | ✓ | ✓ |
| 902 | `ymonsum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 903 | `ymonvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 904 | `ymonvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 905 | `yseasadd` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 906 | `yseasavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 907 | `yseasdiv` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 908 | `yseaseq` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 909 | `yseasge` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 910 | `yseasgt` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 911 | `yseasle` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 912 | `yseaslt` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 913 | `yseasmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 914 | `yseasmean` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 915 | `yseasmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 916 | `yseasmul` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 917 | `yseasne` | Comparison | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 918 | `yseaspctl` | Statistical values | 3→1 | 1 | `ifile1 ifile2 ifile3 ofile pn` | All… | ✓ | ✓ | ✓ |
| 919 | `yseasrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 920 | `yseasstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 921 | `yseasstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 922 | `yseassub` | Arithmetic | 2→1 | 0 | `ifile1 ifile2 ofile` | All… | ✓ | ✓ | ✓ |
| 923 | `yseassum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 924 | `yseasvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 925 | `yseasvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 926 | `zaxisdes` | Information | 1→0 | 0 | `ifile` | All… | ✓ | ✓ | ✓ |
| 927 | `zonavg` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 928 | `zonkurt` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 929 | `zonmax` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 930 | `zonmean` | Statistical values | 1→1 | 1 | `ifile ofile [,zonaldes]` | All… | ✓ | ✓ | ✓ |
| 931 | `zonmedian` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 932 | `zonmin` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 933 | `zonpctl` | Statistical values | 1→1 | 1 | `ifile ofile pn` | All… | ✓ | ✓ | ✓ |
| 934 | `zonrange` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 935 | `zonskew` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 936 | `zonstd` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 937 | `zonstd1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 938 | `zonsum` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 939 | `zonvar` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 940 | `zonvar1` | Statistical values | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
| 941 | `zs2zl` | Miscellaneous | 1→1 | 1 | `ifile ofile levels` | All… | ✓ | ✓ | ✓ |
| 942 | `zs2zlx` | Miscellaneous | 1→1 | 1 | `ifile ofile levels` | All… | ✓ | ✓ | ✓ |
| 943 | `zsdepth` | Miscellaneous | 1→1 | 0 | `ifile ofile` | All… | ✓ | ✓ | ✓ |
